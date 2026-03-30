"""Sub2API 上传能力。"""

from __future__ import annotations

from collections.abc import Iterable
import logging
from datetime import datetime, timezone
import re
from typing import Any, Tuple

from curl_cffi import requests as cffi_requests

from .cpa_upload import _decode_jwt_payload, _get_auth_info

logger = logging.getLogger(__name__)

DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEFAULT_CONCURRENCY = 3
DEFAULT_PRIORITY = 50
MODEL_MAPPING = {
    "gpt-5.1": "gpt-5.1",
    "gpt-5.1-codex": "gpt-5.1-codex",
    "gpt-5.1-codex-max": "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini": "gpt-5.1-codex-mini",
    "gpt-5.2": "gpt-5.2",
    "gpt-5.2-codex": "gpt-5.2-codex",
    "gpt-5.3": "gpt-5.3",
    "gpt-5.3-codex": "gpt-5.3-codex",
    "gpt-5.4": "gpt-5.4",
}


def _get_config_value(key: str, default: str = "") -> str:
    try:
        from core.config_store import config_store

        return str(config_store.get(key, default) or default)
    except Exception:
        return default


def _coerce_int(value, default: int) -> int:
    try:
        if value in (None, ""):
            raise ValueError
        return int(value)
    except Exception:
        return default


def _parse_group_ids(value) -> list[int]:
    if value in (None, ""):
        return []

    if isinstance(value, str):
        items = [part for part in re.split(r"[\s,，]+", value.strip()) if part]
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, dict)):
        items = list(value)
    else:
        items = [value]

    normalized: list[int] = []
    seen: set[int] = set()
    for raw in items:
        group_id = _coerce_int(raw, 0)
        if group_id <= 0:
            raise ValueError("Sub2API 分组 ID 只能填写大于 0 的整数")
        if group_id in seen:
            continue
        seen.add(group_id)
        normalized.append(group_id)
    return normalized


def _iter_accounts(accounts) -> list:
    if isinstance(accounts, (list, tuple, set)):
        return list(accounts)
    if isinstance(accounts, Iterable) and not isinstance(accounts, (str, bytes, dict)):
        return list(accounts)
    return [accounts]


def _normalize_upload_url(api_url: str) -> str:
    base = str(api_url or "").strip().rstrip("/")
    endpoint = "/api/v1/admin/accounts/data"
    if base.endswith(endpoint):
        return base
    return f"{base}{endpoint}"


def _get_attr(account, name: str, default=""):
    value = getattr(account, name, default)
    return default if value is None else value


def _extract_response_data(response) -> Any:
    payload = response.json()
    if isinstance(payload, dict) and "data" in payload:
        return payload.get("data")
    return payload


def _extract_error_message(response, default_message: str) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            return payload.get("message") or payload.get("detail") or payload.get("error") or default_message
    except Exception:
        pass

    text = getattr(response, "text", "") or ""
    if text:
        return f"{default_message} - {text[:200]}"
    return default_message


def _build_headers(api_key: str, idempotency_key: str = "") -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def build_sub2api_account_item(
    account,
    concurrency: int = DEFAULT_CONCURRENCY,
    priority: int = DEFAULT_PRIORITY,
) -> dict:
    extra = _get_attr(account, "extra", {}) or {}
    access_token = (
        _get_attr(account, "access_token", "")
        or extra.get("access_token")
        or _get_attr(account, "token", "")
    )
    if not access_token:
        raise ValueError("账号缺少 access_token")

    payload = _decode_jwt_payload(access_token)
    auth_info = _get_auth_info(payload)
    expires_at = _coerce_int(payload.get("exp"), 0)
    workspace_id = (
        _get_attr(account, "workspace_id", "")
        or extra.get("workspace_id")
        or extra.get("organization_id")
        or auth_info.get("organization_id")
        or ""
    )
    account_id = (
        _get_attr(account, "account_id", "")
        or extra.get("account_id")
        or auth_info.get("chatgpt_account_id")
        or auth_info.get("account_id")
        or _get_attr(account, "user_id", "")
        or ""
    )
    client_id = (
        _get_attr(account, "client_id", "")
        or extra.get("client_id")
        or DEFAULT_CLIENT_ID
    )
    refresh_token = (
        _get_attr(account, "refresh_token", "")
        or extra.get("refresh_token")
        or ""
    )
    email = str(_get_attr(account, "email", "") or extra.get("email") or "").strip()

    return {
        "name": email,
        "platform": "openai",
        "type": "oauth",
        "credentials": {
            "access_token": access_token,
            "chatgpt_account_id": str(account_id or ""),
            "chatgpt_user_id": str(_get_attr(account, "user_id", "") or ""),
            "client_id": str(client_id or DEFAULT_CLIENT_ID),
            "expires_at": expires_at,
            "expires_in": 863999,
            "model_mapping": MODEL_MAPPING,
            "organization_id": str(workspace_id or ""),
            "refresh_token": str(refresh_token or ""),
        },
        "extra": {},
        "concurrency": _coerce_int(concurrency, DEFAULT_CONCURRENCY),
        "priority": _coerce_int(priority, DEFAULT_PRIORITY),
        "rate_multiplier": 1,
        "auto_pause_on_expired": True,
    }


def _local_account_fingerprint(account) -> tuple[str, str, str, str, str]:
    extra = _get_attr(account, "extra", {}) or {}
    return (
        str(_get_attr(account, "email", "") or extra.get("email") or "").strip(),
        str(_get_attr(account, "access_token", "") or extra.get("access_token") or _get_attr(account, "token", "") or "").strip(),
        str(_get_attr(account, "refresh_token", "") or extra.get("refresh_token") or "").strip(),
        str(_get_attr(account, "account_id", "") or extra.get("account_id") or _get_attr(account, "user_id", "") or "").strip(),
        str(_get_attr(account, "client_id", "") or extra.get("client_id") or DEFAULT_CLIENT_ID).strip(),
    )


def _remote_account_fingerprint(item: dict) -> tuple[str, str, str, str, str]:
    credentials = item.get("credentials") or {}
    return (
        str(item.get("name") or "").strip(),
        str(credentials.get("access_token") or "").strip(),
        str(credentials.get("refresh_token") or "").strip(),
        str(credentials.get("chatgpt_account_id") or "").strip(),
        str(credentials.get("client_id") or "").strip(),
    )


def _list_sub2api_accounts(api_url: str, api_key: str, search: str) -> list[dict]:
    url = f"{str(api_url or '').strip().rstrip('/')}/api/v1/admin/accounts"
    page = 1
    page_size = 100
    items: list[dict] = []

    while True:
        response = cffi_requests.get(
            url,
            headers=_build_headers(api_key),
            params={
                "page": page,
                "page_size": page_size,
                "platform": "openai",
                "search": search,
            },
            proxies=None,
            verify=False,
            timeout=30,
            impersonate="chrome110",
        )

        if response.status_code not in (200, 201):
            raise RuntimeError(_extract_error_message(response, f"获取 Sub2API 账号列表失败: HTTP {response.status_code}"))

        data = _extract_response_data(response)
        if isinstance(data, dict):
            page_items = data.get("items") or []
            total = _coerce_int(data.get("total"), len(page_items))
        elif isinstance(data, list):
            page_items = data
            total = len(page_items)
        else:
            page_items = []
            total = 0

        items.extend(item for item in page_items if isinstance(item, dict))
        if not page_items or len(items) >= total:
            break
        page += 1

    return items


def _collect_remote_account_matches(accounts, api_url: str, api_key: str) -> dict[tuple[str, str, str, str, str], list[int]]:
    fingerprints = {
        _local_account_fingerprint(account)
        for account in _iter_accounts(accounts)
        if _local_account_fingerprint(account)[1]
    }
    matches = {fingerprint: [] for fingerprint in fingerprints}

    for search_name in sorted({fingerprint[0] for fingerprint in fingerprints if fingerprint[0]}):
        for item in _list_sub2api_accounts(api_url, api_key, search_name):
            remote_id = item.get("id")
            if remote_id is None:
                continue
            fingerprint = _remote_account_fingerprint(item)
            if fingerprint not in matches:
                continue
            try:
                matches[fingerprint].append(int(remote_id))
            except (TypeError, ValueError):
                continue

    for fingerprint, remote_ids in matches.items():
        matches[fingerprint] = sorted(dict.fromkeys(remote_ids))

    return matches


def _resolve_newly_imported_account_ids(accounts, before_matches, after_matches) -> tuple[list[int], list[str]]:
    counts: dict[tuple[str, str, str, str, str], int] = {}
    for account in _iter_accounts(accounts):
        fingerprint = _local_account_fingerprint(account)
        if not fingerprint[1]:
            continue
        counts[fingerprint] = counts.get(fingerprint, 0) + 1

    new_ids: list[int] = []
    warnings: list[str] = []
    used_ids: set[int] = set()

    for fingerprint, expected_count in counts.items():
        before_ids = set(before_matches.get(fingerprint, []))
        created_ids = [
            remote_id
            for remote_id in after_matches.get(fingerprint, [])
            if remote_id not in before_ids
        ]

        selected_ids: list[int] = []
        for remote_id in created_ids:
            if remote_id in used_ids:
                continue
            used_ids.add(remote_id)
            selected_ids.append(remote_id)
            if len(selected_ids) >= expected_count:
                break

        new_ids.extend(selected_ids)
        if len(selected_ids) < expected_count:
            warnings.append(f"{fingerprint[0]} 仅匹配到 {len(selected_ids)}/{expected_count} 个新导入账号")

    return new_ids, warnings


def _bind_uploaded_accounts_to_groups(account_ids: list[int], group_ids: list[int], api_url: str, api_key: str) -> tuple[bool, str]:
    if not account_ids:
        return False, "未匹配到刚导入的账号，未执行分组绑定"

    url = f"{str(api_url or '').strip().rstrip('/')}/api/v1/admin/accounts/bulk-update"
    response = cffi_requests.post(
        url,
        json={
            "account_ids": account_ids,
            "group_ids": group_ids,
        },
        headers=_build_headers(api_key),
        proxies=None,
        verify=False,
        timeout=30,
        impersonate="chrome110",
    )

    if response.status_code in (200, 201):
        return True, f"已绑定到分组 {group_ids}"
    return False, _extract_error_message(response, f"绑定分组失败: HTTP {response.status_code}")


def build_sub2api_payload(
    accounts,
    concurrency: int = DEFAULT_CONCURRENCY,
    priority: int = DEFAULT_PRIORITY,
) -> dict:
    items = [
        build_sub2api_account_item(
            account,
            concurrency=concurrency,
            priority=priority,
        )
        for account in _iter_accounts(accounts)
    ]

    return {
        "data": {
            "type": "sub2api-data",
            "version": 1,
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "proxies": [],
            "accounts": items,
        },
        "skip_default_group_bind": True,
    }


def upload_to_sub2api(
    accounts,
    api_url: str = None,
    api_key: str = None,
    concurrency: int | None = None,
    priority: int | None = None,
    group_ids=None,
) -> Tuple[bool, str]:
    """按 sub2api-data 格式上传一个或多个 ChatGPT 账号。"""
    api_url = str(api_url or _get_config_value("sub2api_url", "")).strip()
    api_key = str(api_key or _get_config_value("sub2api_api_key", "")).strip()
    concurrency = _coerce_int(
        concurrency if concurrency is not None else _get_config_value("sub2api_concurrency", ""),
        DEFAULT_CONCURRENCY,
    )
    priority = _coerce_int(
        priority if priority is not None else _get_config_value("sub2api_priority", ""),
        DEFAULT_PRIORITY,
    )

    if not api_url:
        return False, "Sub2API URL 未配置"
    if not api_key:
        return False, "Sub2API API Key 未配置"

    account_list = _iter_accounts(accounts)

    try:
        parsed_group_ids = _parse_group_ids(
            group_ids if group_ids is not None else _get_config_value("sub2api_group_ids", "")
        )
        before_matches = {}
        if parsed_group_ids:
            before_matches = _collect_remote_account_matches(account_list, api_url, api_key)
        payload = build_sub2api_payload(account_list, concurrency=concurrency, priority=priority)
    except ValueError as e:
        return False, str(e)
    except Exception as e:
        logger.error("Sub2API 上传前检查异常: %s", e)
        return False, f"上传前检查异常: {e}"

    upload_url = _normalize_upload_url(api_url)
    account_count = len(payload["data"]["accounts"])
    if account_count == 0:
        return False, "无可上传的账号"

    headers = _build_headers(api_key, f"import-{int(datetime.now(timezone.utc).timestamp())}")

    try:
        response = cffi_requests.post(
            upload_url,
            json=payload,
            headers=headers,
            proxies=None,
            verify=False,
            timeout=30,
            impersonate="chrome110",
        )
        if response.status_code in (200, 201):
            message = f"成功上传 {account_count} 个账号到 Sub2API"
            if not parsed_group_ids:
                return True, message

            after_matches = _collect_remote_account_matches(account_list, api_url, api_key)
            created_account_ids, warnings = _resolve_newly_imported_account_ids(
                account_list,
                before_matches,
                after_matches,
            )

            if created_account_ids:
                bind_success, bind_message = _bind_uploaded_accounts_to_groups(
                    created_account_ids,
                    parsed_group_ids,
                    api_url,
                    api_key,
                )
                if bind_success and not warnings and len(created_account_ids) == account_count:
                    return True, f"{message}，并已绑定到分组 {parsed_group_ids}"
                if not bind_success:
                    warnings.append(bind_message)
                else:
                    warnings.append(
                        f"已绑定 {len(created_account_ids)}/{account_count} 个导入账号到分组 {parsed_group_ids}"
                    )
            else:
                warnings.append("未匹配到刚导入的账号，未执行分组绑定")

            warning_text = "；".join(dict.fromkeys(warnings))
            return True, f"{message}，但分组绑定未完全完成：{warning_text}"

        return False, _extract_error_message(response, f"上传失败: HTTP {response.status_code}")
    except Exception as e:
        logger.error("Sub2API 上传异常: %s", e)
        return False, f"上传异常: {e}"
