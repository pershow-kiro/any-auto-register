"""Sub2API 只读状态同步。"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import requests
import urllib3

from platforms.chatgpt.sub2api_upload import DEFAULT_CLIENT_ID
from services.chatgpt_account_state import is_account_deactivated_message

SYNC_RETRY_ATTEMPTS = 3
SYNC_RETRY_DELAY_SECONDS = 0.4
DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_BATCH_WORKERS = 6

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_config_value(key: str, default: str = "") -> str:
    try:
        from core.config_store import config_store

        value = str(config_store.get(key, "") or "").strip()
        return value or default
    except Exception:
        return default


def _base_url(api_url: str | None = None) -> str:
    return str(
        api_url
        or _get_config_value("sub2api_api_url")
        or _get_config_value("sub2api_url")
        or ""
    ).rstrip("/")


def _api_key(api_key: str | None = None) -> str:
    return str(api_key or _get_config_value("sub2api_api_key") or "").strip()


def _headers(api_key: str | None = None) -> dict[str, str]:
    resolved_key = _api_key(api_key)
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
    }
    if resolved_key:
        headers["x-api-key"] = resolved_key
    return headers


def _parse_json_response(response: requests.Response) -> dict[str, Any]:
    if not response.content:
        return {}
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_error_message(response: requests.Response, payload: dict[str, Any], default_message: str) -> str:
    data = payload.get("data")
    candidates = [
        payload.get("message"),
        payload.get("error"),
        data.get("message") if isinstance(data, dict) else "",
        data.get("error") if isinstance(data, dict) else "",
        response.text.strip(),
    ]
    for candidate in candidates:
        if str(candidate or "").strip():
            return str(candidate).strip()[:500]
    return default_message


def _request_json(path: str, *, api_url: str | None = None, api_key: str | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    base_url = _base_url(api_url)
    if not base_url:
        raise RuntimeError("Sub2API URL 未配置")

    resolved_key = _api_key(api_key)
    if not resolved_key:
        raise RuntimeError("Sub2API API Key 未配置")

    target = f"{base_url}{path}"
    try:
        response = requests.get(
            target,
            headers=_headers(resolved_key),
            params=params,
            timeout=DEFAULT_REQUEST_TIMEOUT,
            verify=False,
        )
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(f"Sub2API 无法连接，请确认服务已启动或 API URL 是否正确：{base_url}") from exc
    except requests.exceptions.Timeout as exc:
        raise RuntimeError(f"Sub2API 请求超时：{base_url}") from exc

    payload = _parse_json_response(response)
    if response.status_code not in (200, 201):
        raise RuntimeError(
            _extract_error_message(
                response,
                payload,
                f"获取 Sub2API 账号列表失败: HTTP {response.status_code}",
            )
        )

    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _is_retryable_sync_error(exc: Exception) -> bool:
    text = str(exc or "").strip().lower()
    if not text:
        return False
    markers = (
        "无法连接",
        "请求超时",
        "connection",
        "timeout",
        "timed out",
    )
    return any(marker in text for marker in markers)


def _retry_sync_call(func, *, attempts: int = SYNC_RETRY_ATTEMPTS):
    last_error = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return func()
        except Exception as exc:
            last_error = exc
            if attempt >= attempts or not _is_retryable_sync_error(exc):
                raise
            time.sleep(SYNC_RETRY_DELAY_SECONDS)
    if last_error is not None:
        raise last_error
    raise RuntimeError("sync retry failed without captured error")


def _normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _account_extra(account: Any) -> dict[str, Any]:
    if hasattr(account, "get_extra"):
        try:
            extra = account.get_extra()
            if isinstance(extra, dict):
                return extra
        except Exception:
            pass
    extra = getattr(account, "extra", {})
    return extra if isinstance(extra, dict) else {}


def _local_identity(account: Any) -> dict[str, str]:
    extra = _account_extra(account)
    return {
        "email": _normalize_email(getattr(account, "email", "") or extra.get("email")),
        "access_token": str(getattr(account, "access_token", "") or extra.get("access_token") or getattr(account, "token", "") or "").strip(),
        "refresh_token": str(getattr(account, "refresh_token", "") or extra.get("refresh_token") or "").strip(),
        "account_id": str(
            getattr(account, "account_id", "")
            or extra.get("account_id")
            or extra.get("chatgpt_account_id")
            or getattr(account, "user_id", "")
            or ""
        ).strip(),
        "client_id": str(getattr(account, "client_id", "") or extra.get("client_id") or DEFAULT_CLIENT_ID).strip(),
        "organization_id": str(
            getattr(account, "workspace_id", "")
            or extra.get("workspace_id")
            or extra.get("organization_id")
            or ""
        ).strip(),
    }


def _remote_identity(item: dict[str, Any]) -> dict[str, str]:
    credentials = item.get("credentials") if isinstance(item.get("credentials"), dict) else {}
    return {
        "email": _normalize_email(
            item.get("name")
            or credentials.get("email")
            or ((item.get("extra") or {}).get("email") if isinstance(item.get("extra"), dict) else "")
        ),
        "access_token": str(credentials.get("access_token") or "").strip(),
        "refresh_token": str(credentials.get("refresh_token") or "").strip(),
        "account_id": str(credentials.get("chatgpt_account_id") or credentials.get("account_id") or "").strip(),
        "client_id": str(credentials.get("client_id") or DEFAULT_CLIENT_ID).strip(),
        "organization_id": str(credentials.get("organization_id") or "").strip(),
    }


def _match_score(local_identity: dict[str, str], item: dict[str, Any]) -> int:
    remote_identity = _remote_identity(item)
    if local_identity["email"] and remote_identity["email"] != local_identity["email"]:
        return -1

    score = 0
    if local_identity["email"] and remote_identity["email"] == local_identity["email"]:
        score += 40
    if local_identity["account_id"] and remote_identity["account_id"] == local_identity["account_id"]:
        score += 120
    if local_identity["refresh_token"] and remote_identity["refresh_token"] == local_identity["refresh_token"]:
        score += 80
    if local_identity["access_token"] and remote_identity["access_token"] == local_identity["access_token"]:
        score += 70
    if local_identity["organization_id"] and remote_identity["organization_id"] == local_identity["organization_id"]:
        score += 20
    if local_identity["client_id"] and remote_identity["client_id"] == local_identity["client_id"]:
        score += 10
    return score


def _parse_iso_time(value: Any) -> str:
    text = str(value or "").strip()
    return text


def _infer_remote_state(status: str, schedulable: bool, error_message: str) -> tuple[str, str]:
    normalized_status = str(status or "").strip().lower()
    message = str(error_message or "").strip()
    lowered_message = message.lower()

    if normalized_status == "active":
        if schedulable:
            return "usable", message or "Sub2API 账号正常可调度"
        if "rate limit" in lowered_message or "too many requests" in lowered_message:
            return "quota_exhausted", message or "Sub2API 账号当前被限流"
        return "unschedulable", message or "Sub2API 账号已存在，但当前不可调度"

    if normalized_status == "inactive":
        return "inactive", message or "Sub2API 账号已存在，但当前为 inactive"

    if normalized_status == "error":
        if is_account_deactivated_message("", message):
            return "account_deactivated", message or "远端账号已失效"
        if "token_invalidated" in lowered_message or "access token invalid" in lowered_message or "invalidated" in lowered_message:
            return "access_token_invalidated", message or "远端 Access Token 已失效"
        if "unauthorized" in lowered_message or "401" in lowered_message:
            return "unauthorized", message or "远端账号未授权"
        return "error", message or "Sub2API 账号状态为 error"

    return "unknown", message or f"Sub2API 账号状态未知: {status or 'unknown'}"


def list_sub2api_accounts(search: str, *, api_url: str | None = None, api_key: str | None = None) -> list[dict[str, Any]]:
    search_value = str(search or "").strip()
    page = 1
    page_size = 100
    items: list[dict[str, Any]] = []

    while True:
        payload = _retry_sync_call(
            lambda: _request_json(
                "/api/v1/admin/accounts",
                api_url=api_url,
                api_key=api_key,
                params={
                    "page": page,
                    "page_size": page_size,
                    "platform": "openai",
                    "type": "oauth",
                    "search": search_value,
                },
            )
        )
        page_items = payload.get("items") if isinstance(payload.get("items"), list) else []
        items.extend(item for item in page_items if isinstance(item, dict))

        total = payload.get("total")
        try:
            total_value = int(total or 0)
        except Exception:
            total_value = 0

        if not page_items or (total_value > 0 and len(items) >= total_value):
            break
        page += 1

    return items


def _select_matched_account(account: Any, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    local_identity = _local_identity(account)
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for item in items:
        score = _match_score(local_identity, item)
        if score < 0:
            continue
        updated_at = _parse_iso_time(item.get("updated_at"))
        candidates.append((score, updated_at, item))

    if not candidates:
        return None

    candidates.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return candidates[0][2]


def _build_remote_sync_result(
    account: Any,
    matched: dict[str, Any] | None,
    synced_at: str,
    *,
    api_url: str | None = None,
) -> dict[str, Any]:
    if not matched:
        return {
            "uploaded": False,
            "last_synced_at": synced_at,
            "message": "未在 Sub2API 找到匹配的 OpenAI OAuth 账号",
            "remote_state": "not_found",
            "base_url": _base_url(api_url),
        }

    credentials = matched.get("credentials") if isinstance(matched.get("credentials"), dict) else {}
    status = str(matched.get("status") or "").strip()
    schedulable = bool(matched.get("schedulable"))
    error_message = str(matched.get("error_message") or "").strip()
    remote_state, message = _infer_remote_state(status, schedulable, error_message)

    error_code = ""
    if remote_state == "account_deactivated":
        error_code = "account_deactivated"
    elif remote_state == "access_token_invalidated":
        error_code = "token_invalidated"

    return {
        "uploaded": True,
        "last_synced_at": synced_at,
        "message": message,
        "remote_state": remote_state,
        "base_url": _base_url(api_url),
        "sub2api_id": matched.get("id"),
        "name": str(matched.get("name") or "").strip(),
        "platform": str(matched.get("platform") or "").strip(),
        "type": str(matched.get("type") or "").strip(),
        "status": status,
        "status_message": error_message,
        "schedulable": schedulable,
        "group_ids": matched.get("group_ids") if isinstance(matched.get("group_ids"), list) else [],
        "current_concurrency": matched.get("current_concurrency"),
        "concurrency": matched.get("concurrency"),
        "priority": matched.get("priority"),
        "updated_at": matched.get("updated_at"),
        "created_at": matched.get("created_at"),
        "last_used_at": matched.get("last_used_at"),
        "expires_at": credentials.get("expires_at"),
        "organization_id": str(credentials.get("organization_id") or "").strip(),
        "chatgpt_account_id": str(credentials.get("chatgpt_account_id") or credentials.get("account_id") or "").strip(),
        "client_id": str(credentials.get("client_id") or DEFAULT_CLIENT_ID).strip(),
        "last_probe_at": synced_at,
        "last_probe_status_code": 0,
        "last_probe_error_code": error_code,
        "last_probe_message": message,
    }


def sync_chatgpt_sub2api_status(
    account: Any,
    *,
    api_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    synced_at = _utcnow_iso()
    email = _local_identity(account)["email"]
    try:
        items = list_sub2api_accounts(email, api_url=api_url, api_key=api_key)
    except Exception as exc:
        return {
            "uploaded": False,
            "last_synced_at": synced_at,
            "message": str(exc),
            "remote_state": "unreachable",
            "base_url": _base_url(api_url),
        }

    matched = _select_matched_account(account, items)
    return _build_remote_sync_result(account, matched, synced_at, api_url=api_url)


def sync_chatgpt_sub2api_status_batch(
    accounts: list[Any],
    *,
    api_url: str | None = None,
    api_key: str | None = None,
) -> dict[int, dict[str, Any]]:
    synced_at = _utcnow_iso()
    results: dict[int, dict[str, Any]] = {}
    if not accounts:
        return results

    grouped_accounts: dict[str, list[Any]] = {}
    for account in accounts:
        email = _local_identity(account)["email"]
        grouped_accounts.setdefault(email, []).append(account)

    search_results: dict[str, list[dict[str, Any]]] = {}
    fallback_results: dict[str, dict[str, Any]] = {}
    worker_count = min(DEFAULT_BATCH_WORKERS, max(1, len(grouped_accounts)))

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(list_sub2api_accounts, email, api_url=api_url, api_key=api_key): email
            for email in grouped_accounts
        }

        for future in as_completed(future_map):
            email = future_map[future]
            try:
                search_results[email] = future.result()
            except Exception as exc:
                fallback_results[email] = {
                    "uploaded": False,
                    "last_synced_at": synced_at,
                    "message": str(exc),
                    "remote_state": "unreachable",
                    "base_url": _base_url(api_url),
                }

    for email, email_accounts in grouped_accounts.items():
        if email in fallback_results:
            for account in email_accounts:
                account_id = getattr(account, "id", None)
                if account_id is not None:
                    results[int(account_id)] = dict(fallback_results[email])
            continue

        items = search_results.get(email, [])
        for account in email_accounts:
            account_id = getattr(account, "id", None)
            if account_id is None:
                continue
            matched = _select_matched_account(account, items)
            results[int(account_id)] = _build_remote_sync_result(account, matched, synced_at, api_url=api_url)

    unreachable = sum(1 for item in results.values() if str(item.get("remote_state") or "").strip().lower() == "unreachable")
    not_found = sum(1 for item in results.values() if str(item.get("remote_state") or "").strip().lower() == "not_found")
    logger.info(
        "Sub2API 批量同步完成：accounts=%s, unreachable=%s, not_found=%s, base_url=%s",
        len(results),
        unreachable,
        not_found,
        _base_url(api_url),
    )
    return results
