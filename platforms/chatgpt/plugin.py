"""ChatGPT / Codex CLI 平台插件"""
import random, string
from core.base_platform import BasePlatform, Account, AccountStatus, RegisterConfig
from core.base_mailbox import BaseMailbox
from core.registry import register


@register
class ChatGPTPlatform(BasePlatform):
    name = "chatgpt"
    display_name = "ChatGPT"
    version = "1.0.0"

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox

    def check_valid(self, account: Account) -> bool:
        try:
            from platforms.chatgpt.payment import check_subscription_status
            class _A: pass
            a = _A()
            extra = account.extra or {}
            a.access_token = extra.get("access_token") or account.token
            a.cookies = extra.get("cookies", "")
            status = check_subscription_status(a, proxy=self.config.proxy if self.config else None)
            return status not in ("expired", "invalid", "banned", None)
        except Exception:
            return False

    def register(self, email: str = None, password: str = None) -> Account:
        if not password:
            password = "".join(random.choices(
                string.ascii_letters + string.digits + "!@#$", k=16))

        proxy = self.config.proxy if self.config else None
        browser_mode = (self.config.executor_type if self.config else None) or "protocol"
        log_fn = getattr(self, '_log_fn', print)
        from platforms.chatgpt.register_v2 import (
            RecoverableRegistrationError,
            RegistrationEngineV2 as RegistrationEngine,
        )
        log_fn = getattr(self, '_log_fn', print)
        max_retries = 3
        mail_provider = ""
        if self.config and getattr(self.config, "extra", None):
            try:
                max_retries = int((self.config.extra or {}).get("register_max_retries", 3) or 3)
            except Exception:
                max_retries = 3
            mail_provider = str((self.config.extra or {}).get("mail_provider", "") or "").strip().lower()

        if self.mailbox:
            # 通用 EmailService 适配器，支持所有 BaseMailbox 实现 (cfworker, duckmail, laoudo 等)
            _mailbox = self.mailbox
            _fixed_email = email

            class GenericEmailService:
                service_type = type('ST', (), {'value': 'custom_provider'})()
                def __init__(self):
                    self._acct = None
                    self._email = _fixed_email
                def create_email(self, config=None):
                    if self._email and self._acct and _fixed_email:
                        return {'email': self._email, 'service_id': self._acct.account_id, 'token': ''}
                    self._acct = _mailbox.get_email()
                    if not self._email:
                        self._email = self._acct.email
                    elif not _fixed_email:
                        self._email = self._acct.email
                    return {'email': self._email, 'service_id': self._acct.account_id, 'token': ''}
                def get_verification_code(self, email=None, email_id=None, timeout=120, pattern=None, otp_sent_at=None, exclude_codes=None):
                    if not self._acct:
                        raise RuntimeError("邮箱账户尚未创建，无法获取验证码")
                    return _mailbox.wait_for_code(
                        self._acct,
                        keyword="",
                        timeout=timeout,
                        otp_sent_at=otp_sent_at,
                        exclude_codes=exclude_codes,
                    )
                def update_status(self, success, error=None): pass
                @property
                def status(self): return None

            engine = RegistrationEngine(
                email_service=GenericEmailService(),
                proxy_url=proxy,
                browser_mode=browser_mode,
                callback_logger=log_fn,
                max_retries=max_retries,
                mail_provider=mail_provider,
                extra_config=(self.config.extra or {}),
            )
            engine.email = email
            engine.password = password
        else:
            # 兼容逻辑：若未传入 mailbox 则默认使用 tempmail_lol
            from core.base_mailbox import TempMailLolMailbox
            _tmail = TempMailLolMailbox(proxy=proxy)

            class TempMailEmailService:
                service_type = type('ST', (), {'value': 'tempmail_lol'})()
                def create_email(self, config=None):
                    acct = _tmail.get_email()
                    self._acct = acct
                    return {'email': acct.email, 'service_id': acct.account_id, 'token': acct.account_id}
                def get_verification_code(self, email=None, email_id=None, timeout=120, pattern=None, otp_sent_at=None, exclude_codes=None):
                    return _tmail.wait_for_code(
                        self._acct,
                        keyword="",
                        timeout=timeout,
                        otp_sent_at=otp_sent_at,
                        exclude_codes=exclude_codes,
                    )
                def update_status(self, success, error=None): pass
                @property
                def status(self): return None

            engine = RegistrationEngine(
                email_service=TempMailEmailService(),
                proxy_url=proxy,
                browser_mode=browser_mode,
                callback_logger=log_fn,
                max_retries=max_retries,
                mail_provider=mail_provider,
                extra_config=(self.config.extra or {}),
            )
            if email:
                engine.email = email
                engine.password = password

        result = engine.run()
        if not result or not result.success:
            pending_registration = ((result.metadata or {}).get("pending_registration") if result else None) or {}
            pending_mail_provider = str(
                ((result.metadata or {}).get("mail_provider") if result else "") or mail_provider or ""
            ).strip().lower()
            if pending_registration and pending_mail_provider in ("cloudmail", "cloud_mail"):
                partial_extra = {
                    'mail_provider': pending_mail_provider,
                    'continue_registration_pending': True,
                    'continue_registration_url': pending_registration.get('continue_registration_url', ''),
                    'continue_registration_stage': pending_registration.get('stage', ''),
                    'continue_registration_reason': pending_registration.get('reason', ''),
                    'pending_registration': pending_registration,
                }
                partial_account = Account(
                    platform='chatgpt',
                    email=result.email,
                    password=result.password or password,
                    user_id=result.account_id,
                    token=result.access_token,
                    status=AccountStatus.INVALID,
                    extra=partial_extra,
                )
                raise RecoverableRegistrationError(
                    result.error_message if result else '注册失败',
                    partial_account=partial_account,
                    detail={
                        'pending_registration': pending_registration,
                        'mail_provider': pending_mail_provider,
                        'recoverable': True,
                    },
                )
            raise RuntimeError(result.error_message if result else '注册失败')

        return Account(
            platform='chatgpt',
            email=result.email,
            password=result.password or password,
            user_id=result.account_id,
            token=result.access_token,
            status=AccountStatus.REGISTERED,
            extra={
                'account_id':    result.account_id,
                'access_token':  result.access_token,
                'refresh_token': result.refresh_token,
                'id_token':      result.id_token,
                'session_token': result.session_token,
                'workspace_id':  result.workspace_id,
            },
        )

    def get_platform_actions(self) -> list:
        return [
            {"id": "refresh_token", "label": "刷新 Token", "params": []},
            {"id": "continue_registration", "label": "继续注册并上传", "params": []},
            {"id": "payment_link", "label": "生成支付链接",
             "params": [
                 {"key": "country", "label": "地区", "type": "select",
                  "options": ["US","SG","TR","HK","JP","GB","AU","CA"]},
                 {"key": "plan", "label": "套餐", "type": "select",
                  "options": ["plus", "team"]},
             ]},
            {"id": "upload_cpa", "label": "上传 CPA",
             "params": [
                 {"key": "api_url", "label": "CPA API URL", "type": "text"},
                 {"key": "api_key", "label": "CPA API Key", "type": "text"},
             ]},
            {"id": "upload_tm", "label": "上传 Team Manager",
             "params": [
                 {"key": "api_url", "label": "TM API URL", "type": "text"},
                 {"key": "api_key", "label": "TM API Key", "type": "text"},
             ]},
            {"id": "upload_sub2api", "label": "上传 Sub2API", "params": []},
            {"id": "upload_codex_proxy", "label": "上传 CodexProxy",
             "params": [
                 {"key": "api_url", "label": "API URL", "type": "text"},
                 {"key": "api_key", "label": "Admin Key", "type": "text"},
                 {"key": "upload_type", "label": "上传类型", "type": "select",
                  "options": ["at", "rt"]},
             ]},
        ]

    def execute_action(self, action_id: str, account: Account, params: dict) -> dict:
        proxy = self.config.proxy if self.config else None
        extra = account.extra or {}

        class _A: pass
        a = _A()
        a.email = account.email
        a.access_token = extra.get("access_token") or account.token
        a.refresh_token = extra.get("refresh_token", "")
        a.id_token = extra.get("id_token", "")
        a.session_token = extra.get("session_token", "")
        a.client_id = extra.get("client_id", "app_EMoamEEZ73f0CkXaXp7hrann")
        a.workspace_id = extra.get("workspace_id") or extra.get("organization_id", "")
        a.account_id = extra.get("account_id") or account.user_id or ""
        a.user_id = account.user_id or ""
        a.cookies = extra.get("cookies", "")
        a.extra = extra

        if action_id == "refresh_token":
            from platforms.chatgpt.token_refresh import TokenRefreshManager
            manager = TokenRefreshManager(proxy_url=proxy)
            result = manager.refresh_account(a)
            if result.success:
                return {"ok": True, "data": {"access_token": result.access_token,
                        "refresh_token": result.refresh_token}}
            return {"ok": False, "error": result.error_message}

        elif action_id == "continue_registration":
            from core.base_mailbox import MailboxAccount, create_mailbox
            from services.external_sync import sync_account
            from .chatgpt_client import ChatGPTClient
            from .utils import generate_random_birthday, generate_random_name

            if extra.get("continue_registration_pending") is False and (extra.get("access_token") or account.token):
                return {"ok": False, "error": "当前账号已经完成注册，无需继续注册"}

            mail_provider = str(extra.get("mail_provider") or (self.config.extra or {}).get("mail_provider", "") or "").strip()
            if not mail_provider:
                return {"ok": False, "error": "账号未记录 mail_provider，无法继续注册"}

            mailbox = create_mailbox(
                provider=mail_provider,
                extra=(self.config.extra or {}),
                proxy=proxy,
            )
            mailbox._log_fn = getattr(self, '_log_fn', print)

            class ContinueMailboxAdapter:
                def __init__(self, fixed_email: str):
                    self.account = MailboxAccount(email=fixed_email, account_id=fixed_email)
                    self._used_codes = set()

                def wait_for_verification_code(self, email, timeout=60, otp_sent_at=None, exclude_codes=None):
                    merged_excludes = set(exclude_codes or [])
                    merged_excludes.update(self._used_codes)
                    code = mailbox.wait_for_code(
                        self.account,
                        keyword="",
                        timeout=timeout,
                        otp_sent_at=otp_sent_at,
                        exclude_codes=merged_excludes,
                    )
                    if code:
                        self._used_codes.add(code)
                    return code

            first_name, last_name = generate_random_name()
            birthdate = generate_random_birthday()

            chatgpt_client = ChatGPTClient(
                proxy=proxy,
                verbose=False,
                browser_mode=(self.config.executor_type if self.config else "protocol") or "protocol",
            )
            chatgpt_client._log = getattr(self, '_log_fn', print)

            ok, msg = chatgpt_client.continue_registration_flow(
                account.email,
                account.password,
                first_name,
                last_name,
                birthdate,
                ContinueMailboxAdapter(account.email),
            )
            if not ok:
                return {"ok": False, "error": f"继续注册失败: {msg}"}

            session_ok, session_result = chatgpt_client.reuse_session_and_get_tokens()
            if not session_ok:
                return {"ok": False, "error": f"继续注册成功，但提取 Session 失败: {session_result}"}

            access_token = session_result.get("access_token", "")
            updated_extra = {
                'mail_provider': mail_provider,
                'access_token': access_token,
                'refresh_token': extra.get("refresh_token", ""),
                'id_token': extra.get("id_token", ""),
                'session_token': session_result.get("session_token", ""),
                'workspace_id': session_result.get("workspace_id", ""),
                'account_id': session_result.get("account_id", ""),
                'auth_provider': session_result.get("auth_provider", ""),
                'expires': session_result.get("expires", ""),
                'user': session_result.get("user") or {},
                'account': session_result.get("account") or {},
            }

            account.token = access_token
            account.user_id = session_result.get("account_id") or session_result.get("user_id") or account.user_id
            account.status = AccountStatus.REGISTERED
            account.extra = dict(extra)
            account.extra.update(updated_extra)
            for key in (
                'continue_registration_pending',
                'continue_registration_url',
                'continue_registration_stage',
                'continue_registration_reason',
                'pending_registration',
            ):
                account.extra.pop(key, None)

            upload_results = sync_account(account)

            return {
                "ok": True,
                "data": {
                    "message": "继续注册成功",
                    "access_token": access_token,
                    "session_token": session_result.get("session_token", ""),
                    "workspace_id": session_result.get("workspace_id", ""),
                    "account_id": session_result.get("account_id", ""),
                    "user_id": session_result.get("user_id", ""),
                    "auto_upload_results": upload_results,
                },
                "account_updates": {
                    "status": AccountStatus.REGISTERED.value,
                    "token": access_token,
                    "user_id": account.user_id,
                    "extra_updates": updated_extra,
                    "extra_deletes": [
                        'continue_registration_pending',
                        'continue_registration_url',
                        'continue_registration_stage',
                        'continue_registration_reason',
                        'pending_registration',
                    ],
                },
            }

        elif action_id == "payment_link":
            from platforms.chatgpt.payment import generate_plus_link, generate_team_link
            plan = params.get("plan", "plus")
            country = params.get("country", "US")
            if plan == "plus":
                url = generate_plus_link(a, proxy=proxy, country=country)
            else:
                url = generate_team_link(
                    a,
                    workspace_name=params.get("workspace_name", "MyTeam"),
                    price_interval=params.get("price_interval", "month"),
                    seat_quantity=int(params.get("seat_quantity", 5) or 5),
                    proxy=proxy,
                    country=country,
                )
            return {"ok": bool(url), "data": {"url": url}}

        elif action_id == "upload_cpa":
            from platforms.chatgpt.cpa_upload import upload_to_cpa, generate_token_json
            token_data = generate_token_json(a)
            ok, msg = upload_to_cpa(token_data, api_url=params.get("api_url"),
                                    api_key=params.get("api_key"))
            return {"ok": ok, "data": msg}

        elif action_id == "upload_tm":
            from platforms.chatgpt.cpa_upload import upload_to_team_manager
            ok, msg = upload_to_team_manager(a, api_url=params.get("api_url"),
                                             api_key=params.get("api_key"))
            return {"ok": ok, "data": msg}

        elif action_id == "upload_sub2api":
            from platforms.chatgpt.sub2api_upload import upload_to_sub2api

            ok, msg = upload_to_sub2api(
                a,
                api_url=params.get("api_url"),
                api_key=params.get("api_key"),
            )
            return {"ok": ok, "data": msg}

        elif action_id == "upload_codex_proxy":
            upload_type = str(
                params.get("upload_type")
                or (self.config.extra or {}).get("codex_proxy_upload_type", "at")
                or "at"
            ).strip().lower()
            if upload_type == "at":
                from platforms.chatgpt.cpa_upload import upload_at_to_codex_proxy

                ok, msg = upload_at_to_codex_proxy(
                    a,
                    api_url=params.get("api_url"),
                    api_key=params.get("api_key"),
                )
            else:
                from platforms.chatgpt.cpa_upload import upload_to_codex_proxy

                ok, msg = upload_to_codex_proxy(
                    a,
                    api_url=params.get("api_url"),
                    api_key=params.get("api_key"),
                )
            return {"ok": ok, "data": msg}

        raise NotImplementedError(f"未知操作: {action_id}")
