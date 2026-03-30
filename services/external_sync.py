"""外部系统同步（自动导入 / 回填）"""

from __future__ import annotations

from typing import Any

from services.chatgpt_sync import persist_cpa_sync_result, upload_chatgpt_account_to_cpa


def sync_account(account) -> list[dict[str, Any]]:
    """根据平台将账号同步到外部系统。"""
    from core.config_store import config_store

    platform = getattr(account, "platform", "")
    results: list[dict[str, Any]] = []

    if platform == "chatgpt":
        cpa_url = config_store.get("cpa_api_url", "")
        if cpa_url:
            ok, msg = upload_chatgpt_account_to_cpa(account)
            persist_cpa_sync_result(account, ok, msg)
            results.append({"name": "CPA", "ok": ok, "msg": msg})

        codex_proxy_url = str(config_store.get("codex_proxy_url", "") or "").strip()
        if codex_proxy_url:
            upload_type = str(config_store.get("codex_proxy_upload_type", "at") or "at").strip().lower()
            extra = account.extra or {}

            class _CP:
                pass

            cp = _CP()
            cp.access_token = extra.get("access_token") or account.token
            cp.refresh_token = extra.get("refresh_token", "")

            if upload_type == "rt":
                from platforms.chatgpt.cpa_upload import upload_to_codex_proxy
                ok, msg = upload_to_codex_proxy(cp)
                results.append({"name": "CodexProxy(RT)", "ok": ok, "msg": msg})
            else:
                from platforms.chatgpt.cpa_upload import upload_at_to_codex_proxy
                ok, msg = upload_at_to_codex_proxy(cp)
                results.append({"name": "CodexProxy(AT)", "ok": ok, "msg": msg})

        sub2api_url = str(config_store.get("sub2api_url", "") or "").strip()
        if sub2api_url:
            from platforms.chatgpt.sub2api_upload import upload_to_sub2api

            class _A:
                pass

            a = _A()
            a.email = account.email
            extra = account.extra or {}
            a.access_token = extra.get("access_token") or account.token
            a.refresh_token = extra.get("refresh_token", "")
            a.client_id = extra.get("client_id", "app_EMoamEEZ73f0CkXaXp7hrann")
            a.workspace_id = extra.get("workspace_id") or extra.get("organization_id", "")
            a.account_id = extra.get("account_id") or account.user_id or ""
            a.user_id = account.user_id or ""
            a.extra = extra

            ok, msg = upload_to_sub2api(a)
            results.append({"name": "Sub2API", "ok": ok, "msg": msg})

    elif platform == "grok":
        grok2api_url = str(config_store.get("grok2api_url", "") or "").strip()
        if grok2api_url:
            from services.grok2api_runtime import ensure_grok2api_ready
            from platforms.grok.grok2api_upload import upload_to_grok2api

            ready, ready_msg = ensure_grok2api_ready()
            if not ready:
                results.append({"name": "grok2api", "ok": False, "msg": ready_msg})
                return results

            ok, msg = upload_to_grok2api(account)
            results.append({"name": "grok2api", "ok": ok, "msg": msg})

    elif platform == "kiro":
        from platforms.kiro.account_manager_upload import resolve_manager_path, upload_to_kiro_manager

        configured_path = str(config_store.get("kiro_manager_path", "") or "").strip()
        target_path = resolve_manager_path(configured_path or None)
        if configured_path or target_path.parent.exists() or target_path.exists():
            ok, msg = upload_to_kiro_manager(account, path=configured_path or None)
            results.append({"name": "Kiro Manager", "ok": ok, "msg": msg})

    return results
