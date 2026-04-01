from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/tools", tags=["tools"])

QUOTA_KEYS = (
    "remaining_quota",
    "remainingQuota",
    "available_quota",
    "availableQuota",
    "quota",
    "credits",
    "credit",
    "balance",
    "额度",
    "剩余额度",
    "可用额度",
    "余额",
)
QUOTA_CONTAINER_KEYS = (
    "data",
    "extra",
    "meta",
    "account",
    "profile",
    "stats",
    "usage",
    "subscription",
    "plan",
    "billing",
)
QUOTA_FUZZY_KEYWORDS = ("quota", "credit", "balance", "remaining", "额度", "余额")


class CpaSourceFile(BaseModel):
    name: str
    content: str


class CpaToSub2ApiConvertRequest(BaseModel):
    files: list[CpaSourceFile]
    concurrency: int | None = None
    priority: int | None = None


def _looks_like_cpa_record(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(key in value for key in ("access_token", "refresh_token", "id_token", "account_id"))


def _normalize_cpa_records(parsed: Any, source_name: str) -> list[dict[str, Any]]:
    if isinstance(parsed, dict):
        if _looks_like_cpa_record(parsed):
            return [parsed]

        for key in ("accounts", "items"):
            nested = parsed.get(key)
            if isinstance(nested, list):
                return _normalize_cpa_records(nested, source_name)

        nested_data = parsed.get("data")
        if isinstance(nested_data, list):
            return _normalize_cpa_records(nested_data, source_name)
        if isinstance(nested_data, dict):
            for key in ("accounts", "items"):
                nested = nested_data.get(key)
                if isinstance(nested, list):
                    return _normalize_cpa_records(nested, source_name)

        raise ValueError(f"{source_name} 不是可识别的 CPA 账号格式")

    if isinstance(parsed, list):
        records: list[dict[str, Any]] = []
        for index, item in enumerate(parsed, start=1):
            if not isinstance(item, dict) or not _looks_like_cpa_record(item):
                raise ValueError(f"{source_name} 第 {index} 条记录不是有效的 CPA 账号对象")
            records.append(item)
        return records

    raise ValueError(f"{source_name} 不是可识别的 JSON 对象或数组")


def _parse_cpa_records(raw_content: str, source_name: str) -> list[dict[str, Any]]:
    text = raw_content.lstrip("\ufeff").strip()
    if not text:
        raise ValueError(f"{source_name} 内容为空")

    try:
        return _normalize_cpa_records(json.loads(text), source_name)
    except json.JSONDecodeError as first_error:
        records: list[dict[str, Any]] = []
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            raise ValueError(f"{source_name} 内容为空") from first_error

        for line_number, line in enumerate(lines, start=1):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as line_error:
                raise ValueError(f"{source_name} 第 {line_number} 行不是有效 JSON: {line_error.msg}") from line_error
            records.extend(_normalize_cpa_records(parsed, f"{source_name} 第 {line_number} 行"))

        return records


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_quota_number(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None

    if isinstance(value, bool):
        return Decimal(int(value))

    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None

    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return Decimal(text)
        except InvalidOperation:
            match = re.search(r"-?\d+(?:\.\d+)?", text)
            if not match:
                return None
            try:
                return Decimal(match.group(0))
            except InvalidOperation:
                return None

    if isinstance(value, dict):
        for key in ("value", "quota", "remaining", "available", "credits", "credit", "balance", "amount", "count"):
            parsed = _parse_quota_number(value.get(key))
            if parsed is not None:
                return parsed

    return None


def _iter_quota_candidates(value: Any, prefix: str = "", depth: int = 0):
    if depth > 2 or not isinstance(value, dict):
        return

    for key in QUOTA_KEYS:
        if key in value:
            yield f"{prefix}{key}", value.get(key)

    for key, nested in value.items():
        if isinstance(key, str):
            normalized_key = key.lower()
            if any(keyword in normalized_key for keyword in QUOTA_FUZZY_KEYWORDS):
                yield f"{prefix}{key}", nested

        if isinstance(nested, dict) and (key in QUOTA_CONTAINER_KEYS or depth == 0):
            yield from _iter_quota_candidates(nested, prefix=f"{prefix}{key}.", depth=depth + 1)


def _get_record_quota_info(record: dict[str, Any]) -> tuple[str, Decimal, str] | None:
    for field_name, raw_value in _iter_quota_candidates(record):
        quota = _parse_quota_number(raw_value)
        if quota is None:
            continue
        return field_name, quota, _safe_str(raw_value)
    return None


def _extract_record_email_preview(record: dict[str, Any]) -> str:
    email = _safe_str(record.get("email"))
    if email:
        return email

    access_token = _safe_str(record.get("access_token"))
    if not access_token:
        return ""

    try:
        from platforms.chatgpt.cpa_upload import _decode_jwt_payload

        access_payload = _decode_jwt_payload(access_token)
    except Exception:
        return ""

    return _extract_email(record, access_payload, {})


def _extract_email(record: dict[str, Any], access_payload: dict[str, Any], id_payload: dict[str, Any]) -> str:
    direct_email = _safe_str(record.get("email"))
    if direct_email:
        return direct_email

    for payload in (id_payload, access_payload):
        profile = payload.get("https://api.openai.com/profile")
        if isinstance(profile, dict):
            email = _safe_str(profile.get("email"))
            if email:
                return email

        email = _safe_str(payload.get("email"))
        if email:
            return email

    return ""


def _build_account_from_cpa_record(record: dict[str, Any]):
    from platforms.chatgpt.cpa_upload import _decode_jwt_payload, _get_auth_info

    access_token = _safe_str(record.get("access_token"))
    if not access_token:
        raise ValueError("缺少 access_token")

    id_token = _safe_str(record.get("id_token"))
    access_payload = _decode_jwt_payload(access_token)
    id_payload = _decode_jwt_payload(id_token) if id_token else {}
    auth_info = _get_auth_info(access_payload)
    email = _extract_email(record, access_payload, id_payload)
    if not email:
        raise ValueError("缺少 email")

    client_id = (
        _safe_str(record.get("client_id"))
        or _safe_str(access_payload.get("client_id"))
        or "app_EMoamEEZ73f0CkXaXp7hrann"
    )

    return SimpleNamespace(
        email=email,
        access_token=access_token,
        refresh_token=_safe_str(record.get("refresh_token")),
        id_token=id_token,
        account_id=(
            _safe_str(record.get("account_id"))
            or _safe_str(auth_info.get("chatgpt_account_id"))
            or _safe_str(auth_info.get("account_id"))
        ),
        user_id=(
            _safe_str(record.get("user_id"))
            or _safe_str(auth_info.get("chatgpt_user_id"))
            or _safe_str(auth_info.get("user_id"))
            or _safe_str(access_payload.get("sub"))
        ),
        workspace_id=(
            _safe_str(record.get("workspace_id"))
            or _safe_str(auth_info.get("organization_id"))
        ),
        client_id=client_id,
        extra={},
    )


def _resolve_sub2api_options(body: CpaToSub2ApiConvertRequest) -> tuple[int, int]:
    from platforms.chatgpt.sub2api_upload import (
        DEFAULT_CONCURRENCY,
        DEFAULT_PRIORITY,
    )

    concurrency = body.concurrency if body.concurrency and body.concurrency > 0 else DEFAULT_CONCURRENCY
    priority = body.priority if body.priority is not None else DEFAULT_PRIORITY
    return concurrency, priority


def _convert_cpa_files(files: list[CpaSourceFile]) -> dict[str, Any]:
    if not files:
        raise HTTPException(400, "请至少上传一个文件")

    converted_accounts = []
    success_items: list[dict[str, Any]] = []
    skipped_items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    file_summaries: list[dict[str, Any]] = []
    total_records = 0

    for source_file in files:
        source_name = _safe_str(source_file.name) or "未命名文件"
        file_total = 0
        file_success = 0
        file_skipped = 0
        file_failed = 0

        try:
            records = _parse_cpa_records(source_file.content, source_name)
        except ValueError as e:
            errors.append({"source": source_name, "message": str(e)})
            file_summaries.append(
                {
                    "name": source_name,
                    "records": 0,
                    "success": 0,
                    "skipped": 0,
                    "failed": 1,
                }
            )
            continue

        for record_index, record in enumerate(records, start=1):
            file_total += 1
            total_records += 1

            quota_info = _get_record_quota_info(record)
            if quota_info is not None:
                quota_field, quota_value, quota_display = quota_info
                if quota_value <= 0:
                    skipped_items.append(
                        {
                            "source": source_name,
                            "index": record_index,
                            "email": _extract_record_email_preview(record),
                            "quota_field": quota_field,
                            "quota_value": quota_display or str(quota_value),
                            "message": "额度为 0，已跳过，不导出到 Sub2API",
                        }
                    )
                    file_skipped += 1
                    continue

            try:
                account = _build_account_from_cpa_record(record)
                converted_accounts.append(account)
                success_items.append(
                    {
                        "source": source_name,
                        "index": record_index,
                        "email": account.email,
                        "account_id": account.account_id,
                    }
                )
                file_success += 1
            except ValueError as e:
                errors.append(
                    {
                        "source": source_name,
                        "index": record_index,
                        "message": str(e),
                    }
                )
                file_failed += 1

        file_summaries.append(
            {
                "name": source_name,
                "records": file_total,
                "success": file_success,
                "skipped": file_skipped,
                "failed": file_failed,
            }
        )

    return {
        "converted_accounts": converted_accounts,
        "success_items": success_items,
        "skipped_items": skipped_items,
        "errors": errors,
        "file_summaries": file_summaries,
        "total_records": total_records,
    }


def _build_sub2api_preview(
    converted_accounts,
    success_items: list[dict[str, Any]],
    *,
    concurrency: int,
    priority: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    from platforms.chatgpt.sub2api_upload import build_sub2api_payload

    payload = None
    preview_items: list[dict[str, Any]] = []
    if converted_accounts:
        payload = build_sub2api_payload(
            converted_accounts,
            concurrency=concurrency,
            priority=priority,
        )
        for source, item in zip(success_items, payload["data"]["accounts"], strict=False):
            credentials = item.get("credentials") or {}
            preview_items.append(
                {
                    "source": source["source"],
                    "index": source["index"],
                    "email": item.get("name") or source["email"],
                    "account_id": credentials.get("chatgpt_account_id") or source["account_id"],
                    "organization_id": credentials.get("organization_id") or "",
                    "client_id": credentials.get("client_id") or "",
                    "expires_at": credentials.get("expires_at") or 0,
                }
            )

    return payload, preview_items


def _build_convert_response(
    *,
    files: list[CpaSourceFile],
    concurrency: int,
    priority: int,
    conversion: dict[str, Any],
    upload_attempted: bool = False,
    upload_success: bool = False,
    upload_message: str = "",
    upload_logs: list[str] | None = None,
) -> dict[str, Any]:
    converted_accounts = conversion["converted_accounts"]
    success_items = conversion["success_items"]
    skipped_items = conversion["skipped_items"]
    errors = conversion["errors"]
    file_summaries = conversion["file_summaries"]
    total_records = conversion["total_records"]

    payload, preview_items = _build_sub2api_preview(
        converted_accounts,
        success_items,
        concurrency=concurrency,
        priority=priority,
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    return {
        "total_files": len(files),
        "total_records": total_records,
        "success_count": len(converted_accounts),
        "skipped_count": len(skipped_items),
        "failed_count": len(errors),
        "file_summaries": file_summaries,
        "skipped_items": skipped_items,
        "errors": errors,
        "payload": payload,
        "items": preview_items,
        "download_name": f"sub2api-data-{timestamp}.json",
        "upload_attempted": upload_attempted,
        "upload_success": upload_success,
        "upload_message": upload_message,
        "upload_logs": upload_logs or [],
    }


@router.post("/cpa-to-sub2api")
def convert_cpa_to_sub2api(body: CpaToSub2ApiConvertRequest):
    concurrency, priority = _resolve_sub2api_options(body)
    conversion = _convert_cpa_files(body.files)
    return _build_convert_response(
        files=body.files,
        concurrency=concurrency,
        priority=priority,
        conversion=conversion,
    )


@router.post("/cpa-to-sub2api-upload")
def convert_and_upload_cpa_to_sub2api(body: CpaToSub2ApiConvertRequest):
    from platforms.chatgpt.sub2api_upload import upload_to_sub2api

    concurrency, priority = _resolve_sub2api_options(body)
    conversion = _convert_cpa_files(body.files)
    converted_accounts = conversion["converted_accounts"]

    upload_attempted = False
    upload_success = False
    upload_message = ""
    upload_logs: list[str] = []

    if converted_accounts:
        upload_attempted = True
        upload_success, upload_message = upload_to_sub2api(
            converted_accounts,
            concurrency=concurrency,
            priority=priority,
            log_messages=upload_logs,
        )
    else:
        upload_message = "没有可上传的 Sub2API 账号"
        upload_logs.append(upload_message)

    return _build_convert_response(
        files=body.files,
        concurrency=concurrency,
        priority=priority,
        conversion=conversion,
        upload_attempted=upload_attempted,
        upload_success=upload_success,
        upload_message=upload_message,
        upload_logs=upload_logs,
    )
