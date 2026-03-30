"""Cloud Mail 公共 API 邮箱适配器。"""

from __future__ import annotations

import random
import string
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from .base_mailbox import BaseMailbox, MailboxAccount


OTP_SENT_AT_TOLERANCE_SECONDS = 2


class CloudMailMailbox(BaseMailbox):
    """基于 maillab/cloud-mail public API 的邮箱实现。"""

    def __init__(
        self,
        api_url: str,
        admin_email: str,
        admin_password: str,
        domain: str,
        proxy: str = None,
    ):
        self.api = str(api_url or "").strip().rstrip("/")
        self.admin_email = str(admin_email or "").strip()
        self.admin_password = str(admin_password or "").strip()
        self.domain = str(domain or "").strip().lstrip("@")
        self.proxy = {"http": proxy, "https": proxy} if proxy else None
        self._public_token = ""

    def _require_config(self) -> None:
        missing = []
        if not self.api:
            missing.append("cloudmail_api_url")
        if not self.admin_email:
            missing.append("cloudmail_admin_email")
        if not self.admin_password:
            missing.append("cloudmail_admin_password")
        if not self.domain:
            missing.append("cloudmail_domain")
        if missing:
            raise RuntimeError(
                "Cloud Mail 未配置完整，请在全局设置中填写: " + ", ".join(missing)
            )

    def _base_url(self) -> str:
        if self.api.endswith("/api"):
            return self.api[:-4]
        return self.api

    def _headers(self, token: str = "") -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if token:
            headers["Authorization"] = token
        return headers

    def _unwrap_payload(self, payload: Any) -> Any:
        if not isinstance(payload, dict) or "code" not in payload:
            return payload
        if payload.get("code") != 200:
            raise RuntimeError(str(payload.get("message") or "Cloud Mail API 返回失败"))
        return payload.get("data")

    def _request(self, method: str, path: str, token: str = "", json_body: dict | None = None) -> Any:
        self._require_config()
        url = f"{self._base_url()}/api{path}"
        resp = requests.request(
            method,
            url,
            json=json_body,
            headers=self._headers(token),
            proxies=self.proxy,
            timeout=20,
        )
        if resp.status_code >= 400:
            detail = resp.text[:200]
            try:
                detail = str(resp.json())
            except Exception:
                pass
            raise RuntimeError(f"Cloud Mail 请求失败: HTTP {resp.status_code} - {detail}")

        try:
            payload = resp.json()
        except Exception:
            payload = {"raw_response": resp.text}
        return self._unwrap_payload(payload)

    def _get_public_token(self, force_refresh: bool = False) -> str:
        if self._public_token and not force_refresh:
            return self._public_token

        data = self._request(
            "POST",
            "/public/genToken",
            json_body={
                "email": self.admin_email,
                "password": self.admin_password,
            },
        )

        token = ""
        if isinstance(data, dict):
            token = str(data.get("token") or "").strip()
        else:
            token = str(data or "").strip()
        if not token:
            raise RuntimeError("Cloud Mail 未返回 public token")

        self._public_token = token
        return token

    def _generate_local_part(self) -> str:
        first = random.choice(string.ascii_lowercase)
        rest = "".join(random.choices(string.ascii_lowercase + string.digits, k=7))
        return f"{first}{rest}"

    def _generate_password(self) -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(random.choices(alphabet, k=16))

    def _parse_message_time(self, value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            timestamp = float(value)
        else:
            text = str(value).strip()
            if not text:
                return None
            try:
                timestamp = float(text)
            except ValueError:
                normalized = text.replace("Z", "+00:00")
                if "T" not in normalized and "+" not in normalized[10:] and normalized.count(":") >= 2:
                    normalized = normalized.replace(" ", "T", 1) + "+00:00"
                try:
                    parsed = datetime.fromisoformat(normalized)
                except ValueError:
                    return None
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                timestamp = parsed.astimezone(timezone.utc).timestamp()
        while timestamp > 1e11:
            timestamp /= 1000.0
        return timestamp if timestamp > 0 else None

    def _get_received_timestamp(self, mail: dict[str, Any]) -> Optional[float]:
        for field_name in ("createTime", "createdAt", "receivedAt", "timestamp", "time"):
            timestamp = self._parse_message_time(mail.get(field_name))
            if timestamp is not None:
                return timestamp
        return None

    def _list_mails(self, email: str) -> list[dict[str, Any]]:
        token = self._get_public_token(force_refresh=True)
        data = self._request(
            "POST",
            "/public/emailList",
            token=token,
            json_body={
                "toEmail": email,
                "num": 1,
                "size": 20,
            },
        )
        if isinstance(data, dict) and isinstance(data.get("list"), list):
            data = data["list"]
        return data if isinstance(data, list) else []

    def get_email(self) -> MailboxAccount:
        address = f"{self._generate_local_part()}@{self.domain}"
        password = self._generate_password()
        token = self._get_public_token()
        self._request(
            "POST",
            "/public/addUser",
            token=token,
            json_body={
                "list": [
                    {
                        "email": address,
                        "password": password,
                    }
                ]
            },
        )
        self._log(f"[CloudMail] 生成邮箱: {address}")
        return MailboxAccount(
            email=address,
            account_id=address,
            extra={"password": password},
        )

    def get_current_ids(self, account: MailboxAccount) -> set:
        try:
            return {
                str(mail.get("emailId") or mail.get("id"))
                for mail in self._list_mails(account.email)
                if mail.get("emailId") or mail.get("id")
            }
        except Exception:
            return set()

    def wait_for_code(
        self,
        account: MailboxAccount,
        keyword: str = "",
        timeout: int = 120,
        before_ids: set = None,
        code_pattern: str = None,
        **kwargs,
    ) -> str:
        seen = {str(item) for item in (before_ids or set())}
        exclude_codes = {str(item) for item in (kwargs.get("exclude_codes") or []) if item}
        otp_sent_at = kwargs.get("otp_sent_at")
        min_allowed_timestamp = (
            float(otp_sent_at) - OTP_SENT_AT_TOLERANCE_SECONDS if otp_sent_at else None
        )
        start = time.time()

        while time.time() - start < timeout:
            try:
                mails = self._list_mails(account.email)
                mails.sort(
                    key=lambda item: self._get_received_timestamp(item) or float(item.get("emailId") or item.get("id") or 0),
                    reverse=True,
                )
                for mail in mails:
                    mail_id = str(mail.get("emailId") or mail.get("id") or "")
                    if not mail_id or mail_id in seen:
                        continue

                    mail_timestamp = self._get_received_timestamp(mail)
                    if min_allowed_timestamp is not None and (
                        mail_timestamp is None or mail_timestamp <= min_allowed_timestamp
                    ):
                        continue

                    seen.add(mail_id)
                    parts = [
                        str(mail.get("sendEmail") or mail.get("sender") or ""),
                        str(mail.get("sendName") or mail.get("name") or ""),
                        str(mail.get("subject") or ""),
                        str(mail.get("text") or ""),
                        self._decode_raw_content(str(mail.get("content") or "")),
                    ]
                    search_text = "\n".join(part for part in parts if part).strip()
                    if keyword and keyword.lower() not in search_text.lower():
                        continue

                    code = self._safe_extract(search_text, code_pattern)
                    if code and code in exclude_codes:
                        continue
                    if code:
                        self._log(f"[CloudMail] 命中验证码: {code}")
                        return code
            except Exception:
                pass

            time.sleep(3)

        raise TimeoutError(f"等待验证码超时 ({timeout}s)")
