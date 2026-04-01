"""Sub2API 上传能力。"""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from datetime import datetime, timezone
import re
import time
from typing import Any, Tuple

from curl_cffi import requests as cffi_requests

from .cpa_upload import _decode_jwt_payload, _get_auth_info

logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False

DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEFAULT_CONCURRENCY = 3
DEFAULT_PRIORITY = 50
DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_REQUEST_RETRIES = 3
DEFAULT_MATCH_LOOKUP_WORKERS = 6
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

        if key == "sub2api_url":
            return str(
                config_store.get("sub2api_url", "")
                or config_store.get("sub2api_api_url", default)
                or default
            )
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


def _record_log(log_messages: list[str] | None, message: str, level: str = "info") -> None:
    if log_messages is not None:
        log_messages.append(message)

    log_fn = getattr(logger, level, logger.info)
    log_fn(message)


def _build_headers(api_key: str, idempotency_key: str = "") -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _perform_sub2api_request(
    method: str,
    url: str,
    *,
    log_messages: list[str] | None = None,
    request_label: str = "",
    **kwargs,
):
    request_fn = getattr(cffi_requests, method)
    last_error = None
    label = request_label or f"Sub2API {method.upper()} {url}"

    for attempt in range(1, DEFAULT_REQUEST_RETRIES + 1):
        try:
            return request_fn(
                url,
                proxies=None,
                verify=False,
                timeout=DEFAULT_REQUEST_TIMEOUT,
                impersonate="chrome110",
                **kwargs,
            )
        except Exception as e:
            last_error = e
            if attempt >= DEFAULT_REQUEST_RETRIES:
                raise
            _record_log(
                log_messages,
                f"{label} 请求异常，第 {attempt}/{DEFAULT_REQUEST_RETRIES} 次重试: {e}",
                "warning",
            )
            time.sleep(min(attempt, 3))

    raise last_error


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


def _coerce_positive_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _collect_local_fingerprints(accounts) -> list[tuple[str, str, str, str, str]]:
    fingerprints: list[tuple[str, str, str, str, str]] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    for account in _iter_accounts(accounts):
        fingerprint = _local_account_fingerprint(account)
        if not fingerprint[1] or fingerprint in seen:
            continue
        seen.add(fingerprint)
        fingerprints.append(fingerprint)

    return fingerprints


def _list_sub2api_accounts(
    api_url: str,
    api_key: str,
    search: str,
    *,
    log_messages: list[str] | None = None,
    stage_label: str = "远端账号匹配检查",
    progress_index: int | None = None,
    progress_total: int | None = None,
) -> list[dict]:
    url = f"{str(api_url or '').strip().rstrip('/')}/api/v1/admin/accounts"
    page = 1
    page_size = 100
    items: list[dict] = []
    search_label = search or "<空邮箱>"
    progress_label = stage_label
    if progress_index is not None and progress_total is not None:
        progress_label = f"{stage_label} {progress_index}/{progress_total}"

    _record_log(log_messages, f"{progress_label} 开始检查邮箱：{search_label}")

    while True:
        page_label = f"{progress_label} 邮箱 {search_label} 第 {page} 页"
        _record_log(log_messages, f"{page_label} 请求中")
        started_at = time.perf_counter()
        response = _perform_sub2api_request(
            "get",
            url,
            log_messages=log_messages,
            request_label=page_label,
            headers=_build_headers(api_key),
            params={
                "page": page,
                "page_size": page_size,
                "platform": "openai",
                "search": search,
            },
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
        elapsed = time.perf_counter() - started_at
        total_label = total if total > 0 else len(items)
        _record_log(
            log_messages,
            f"{page_label} 完成：本页 {len(page_items)} 条，累计 {len(items)}/{total_label}，耗时 {elapsed:.1f}s",
        )
        if not page_items or len(items) >= total:
            break
        page += 1

    _record_log(log_messages, f"{progress_label} 邮箱 {search_label} 检查完成，命中 {len(items)} 条")
    return items


def _collect_remote_account_matches(
    accounts,
    api_url: str,
    api_key: str,
    *,
    log_messages: list[str] | None = None,
    stage_label: str = "远端账号匹配检查",
) -> dict[tuple[str, str, str, str, str], list[int]]:
    fingerprints = _collect_local_fingerprints(accounts)
    matches = {fingerprint: [] for fingerprint in fingerprints}
    search_names = sorted({fingerprint[0] for fingerprint in fingerprints if fingerprint[0]})

    if not search_names:
        _record_log(log_messages, f"{stage_label}：没有可用于匹配的邮箱，跳过检查", "warning")
        return matches

    worker_count = min(DEFAULT_MATCH_LOOKUP_WORKERS, len(search_names))
    _record_log(
        log_messages,
        f"{stage_label}：开始检查 {len(search_names)} 个邮箱，使用 {worker_count} 个并发任务",
    )

    search_results: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(
                _list_sub2api_accounts,
                api_url,
                api_key,
                search_name,
                log_messages=log_messages,
                stage_label=stage_label,
                progress_index=index,
                progress_total=len(search_names),
            ): search_name
            for index, search_name in enumerate(search_names, start=1)
        }

        for future in as_completed(future_map):
            search_name = future_map[future]
            try:
                search_results[search_name] = future.result()
            except Exception as e:
                _record_log(log_messages, f"{stage_label}：邮箱 {search_name} 检查失败: {e}", "error")
                raise

    for search_name in search_names:
        for item in search_results.get(search_name, []):
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

    _record_log(log_messages, f"{stage_label}：完成，唯一账号指纹 {len(matches)} 个")
    return matches


def _extract_created_account_ids_from_payload(payload: Any) -> list[int]:
    collected_ids: list[int] = []
    seen: set[int] = set()

    def add_id(raw_value) -> None:
        parsed = _coerce_positive_int(raw_value)
        if parsed is None or parsed in seen:
            return
        seen.add(parsed)
        collected_ids.append(parsed)

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 5 or value is None:
            return

        if isinstance(value, dict):
            for key in ("account_ids", "created_ids", "inserted_ids", "imported_ids"):
                candidate_ids = value.get(key)
                if isinstance(candidate_ids, Iterable) and not isinstance(candidate_ids, (str, bytes, dict)):
                    for candidate_id in candidate_ids:
                        add_id(candidate_id)

            if "id" in value and any(key in value for key in ("platform", "credentials", "name", "type")):
                add_id(value.get("id"))

            for nested in value.values():
                if isinstance(nested, (dict, list, tuple, set)):
                    walk(nested, depth + 1)
            return

        if isinstance(value, (list, tuple, set)):
            for item in value:
                walk(item, depth + 1)

    walk(payload)
    return collected_ids


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


def _bind_uploaded_accounts_to_groups(
    account_ids: list[int],
    group_ids: list[int],
    api_url: str,
    api_key: str,
    *,
    log_messages: list[str] | None = None,
) -> tuple[bool, str]:
    if not account_ids:
        return False, "未匹配到刚导入的账号，未执行分组绑定"

    url = f"{str(api_url or '').strip().rstrip('/')}/api/v1/admin/accounts/bulk-update"
    response = _perform_sub2api_request(
        "post",
        url,
        log_messages=log_messages,
        request_label=f"Sub2API 分组绑定 {group_ids}",
        json={
            "account_ids": account_ids,
            "group_ids": group_ids,
        },
        headers=_build_headers(api_key),
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
    log_messages: list[str] | None = None,
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
        _record_log(log_messages, "Sub2API URL 未配置", "error")
        return False, "Sub2API URL 未配置"
    if not api_key:
        _record_log(log_messages, "Sub2API API Key 未配置", "error")
        return False, "Sub2API API Key 未配置"

    account_list = _iter_accounts(accounts)
    _record_log(
        log_messages,
        f"准备上传到 Sub2API：账号 {len(account_list)} 个，并发 {concurrency}，优先级 {priority}",
    )
    parsed_group_ids: list[int] = []
    before_matches = {}
    precheck_warning = ""
    skip_group_binding = False

    try:
        parsed_group_ids = _parse_group_ids(
            group_ids if group_ids is not None else _get_config_value("sub2api_group_ids", "")
        )
        if parsed_group_ids:
            _record_log(log_messages, f"检测到分组绑定配置：{parsed_group_ids}")
        else:
            _record_log(log_messages, "未配置分组绑定，将只执行账号导入")
        if parsed_group_ids:
            try:
                _record_log(log_messages, "开始执行上传前远端账号匹配检查")
                before_matches = _collect_remote_account_matches(
                    account_list,
                    api_url,
                    api_key,
                    log_messages=log_messages,
                    stage_label="上传前远端账号匹配检查",
                )
                _record_log(log_messages, "上传前远端账号匹配检查完成")
            except Exception as e:
                skip_group_binding = True
                precheck_warning = f"上传前检查异常，已跳过自动绑组: {e}"
                _record_log(log_messages, precheck_warning, "warning")
        payload = build_sub2api_payload(account_list, concurrency=concurrency, priority=priority)
    except ValueError as e:
        _record_log(log_messages, f"上传前校验失败: {e}", "error")
        return False, str(e)
    except Exception as e:
        _record_log(log_messages, f"上传前检查异常: {e}", "error")
        return False, f"上传前检查异常: {e}"

    upload_url = _normalize_upload_url(api_url)
    account_count = len(payload["data"]["accounts"])
    if account_count == 0:
        _record_log(log_messages, "无可上传的账号", "warning")
        return False, "无可上传的账号"

    headers = _build_headers(api_key, f"import-{int(datetime.now(timezone.utc).timestamp())}")
    _record_log(log_messages, f"开始请求 Sub2API 导入接口：{upload_url}")

    try:
        response = _perform_sub2api_request(
            "post",
            upload_url,
            log_messages=log_messages,
            request_label="Sub2API 导入接口",
            json=payload,
            headers=headers,
        )
        if response.status_code in (200, 201):
            message = f"成功上传 {account_count} 个账号到 Sub2API"
            _record_log(log_messages, message)
            if not parsed_group_ids:
                return True, message
            if skip_group_binding:
                _record_log(log_messages, f"分组绑定未执行：{precheck_warning}", "warning")
                return True, f"{message}，但分组绑定未执行：{precheck_warning}"

            warnings: list[str] = []
            try:
                try:
                    response_data = _extract_response_data(response)
                except Exception as e:
                    response_data = None
                    _record_log(log_messages, f"导入响应解析失败，将回退到上传后远端匹配检查: {e}", "warning")
                direct_created_account_ids = _extract_created_account_ids_from_payload(response_data)
                if direct_created_account_ids:
                    _record_log(
                        log_messages,
                        f"从导入响应中直接提取到 {len(direct_created_account_ids)} 个账号 ID",
                    )
                else:
                    _record_log(log_messages, "导入响应中未直接返回可绑定的账号 ID")

                created_account_ids: list[int] = []
                resolve_warnings: list[str] = []
                if len(direct_created_account_ids) == account_count:
                    created_account_ids = direct_created_account_ids
                    _record_log(log_messages, "导入响应已提供完整账号 ID，跳过上传后远端匹配检查")
                else:
                    if direct_created_account_ids:
                        _record_log(
                            log_messages,
                            f"导入响应返回的账号 ID 数量不足（{len(direct_created_account_ids)}/{account_count}），回退到上传后远端匹配检查",
                            "warning",
                        )
                    _record_log(log_messages, "开始上传后远端账号匹配，用于分组绑定")
                    after_matches = _collect_remote_account_matches(
                        account_list,
                        api_url,
                        api_key,
                        log_messages=log_messages,
                        stage_label="上传后远端账号匹配检查",
                    )
                    created_account_ids, resolve_warnings = _resolve_newly_imported_account_ids(
                        account_list,
                        before_matches,
                        after_matches,
                    )

                if created_account_ids:
                    _record_log(log_messages, f"已匹配到 {len(created_account_ids)} 个新导入账号，开始绑定分组 {parsed_group_ids}")
                    bind_success, bind_message = _bind_uploaded_accounts_to_groups(
                        created_account_ids,
                        parsed_group_ids,
                        api_url,
                        api_key,
                        log_messages=log_messages,
                    )
                    if bind_success and not resolve_warnings and len(created_account_ids) == account_count:
                        _record_log(log_messages, f"分组绑定完成：{parsed_group_ids}")
                        return True, f"{message}，并已绑定到分组 {parsed_group_ids}"
                    if not bind_success:
                        warnings.append(bind_message)
                        _record_log(log_messages, f"分组绑定失败：{bind_message}", "warning")
                    else:
                        partial_message = f"已绑定 {len(created_account_ids)}/{account_count} 个导入账号到分组 {parsed_group_ids}"
                        warnings.append(partial_message)
                        _record_log(log_messages, partial_message, "warning")
                else:
                    warnings.append("未匹配到刚导入的账号，未执行分组绑定")
                    _record_log(log_messages, "未匹配到刚导入的账号，未执行分组绑定", "warning")

                warnings.extend(resolve_warnings)
                for resolve_warning in resolve_warnings:
                    _record_log(log_messages, resolve_warning, "warning")
            except Exception as e:
                warning_message = f"分组绑定异常: {e}"
                _record_log(log_messages, warning_message, "warning")
                warnings.append(warning_message)

            warning_text = "；".join(dict.fromkeys(warnings))
            return True, f"{message}，但分组绑定未完全完成：{warning_text}"

        error_message = _extract_error_message(response, f"上传失败: HTTP {response.status_code}")
        _record_log(log_messages, error_message, "error")
        return False, error_message
    except Exception as e:
        _record_log(log_messages, f"上传异常: {e}", "error")
        return False, f"上传异常: {e}"
