from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select, func
from pydantic import BaseModel
from core.db import AccountModel, get_session
from typing import Optional
from datetime import datetime, timezone
import io, csv, json, logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/accounts", tags=["accounts"])


class AccountCreate(BaseModel):
    platform: str
    email: str
    password: str
    status: str = "registered"
    token: str = ""
    cashier_url: str = ""


class AccountUpdate(BaseModel):
    status: Optional[str] = None
    token: Optional[str] = None
    cashier_url: Optional[str] = None


class ImportRequest(BaseModel):
    platform: str
    lines: list[str]


class BatchDeleteRequest(BaseModel):
    ids: list[int] = []
    all_filtered: bool = False
    platform: Optional[str] = None
    status: Optional[str] = None
    email: Optional[str] = None


class BatchSub2ApiUploadRequest(BaseModel):
    ids: list[int]
    group_ids: list[int] | None = None


def _apply_account_filters(query, platform: Optional[str] = None,
                           status: Optional[str] = None,
                           email: Optional[str] = None):
    if platform:
        query = query.where(AccountModel.platform == platform)
    if status:
        query = query.where(AccountModel.status == status)
    if email:
        query = query.where(AccountModel.email.contains(email))
    return query


def _apply_account_order(query):
    return query.order_by(AccountModel.created_at.desc(), AccountModel.id.desc())


def _to_chatgpt_upload_account(acc: AccountModel):
    extra = acc.get_extra()

    class _Acc:
        pass

    a = _Acc()
    a.email = acc.email
    a.access_token = extra.get("access_token") or acc.token
    a.refresh_token = extra.get("refresh_token", "")
    a.client_id = extra.get("client_id", "app_EMoamEEZ73f0CkXaXp7hrann")
    a.workspace_id = extra.get("workspace_id") or extra.get("organization_id", "")
    a.account_id = extra.get("account_id") or acc.user_id or ""
    a.user_id = acc.user_id or ""
    a.extra = extra
    return a


@router.get("")
def list_accounts(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    email: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    session: Session = Depends(get_session),
):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)

    total_query = _apply_account_filters(
        select(func.count()).select_from(AccountModel),
        platform=platform,
        status=status,
        email=email,
    )
    total = session.exec(total_query).one() or 0

    items_query = _apply_account_order(
        _apply_account_filters(
            select(AccountModel),
            platform=platform,
            status=status,
            email=email,
        )
    )
    items = session.exec(
        items_query.offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.post("")
def create_account(body: AccountCreate, session: Session = Depends(get_session)):
    acc = AccountModel(
        platform=body.platform,
        email=body.email,
        password=body.password,
        status=body.status,
        token=body.token,
        cashier_url=body.cashier_url,
    )
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


@router.get("/stats")
def get_stats(session: Session = Depends(get_session)):
    """统计各平台账号数量和状态分布"""
    accounts = session.exec(select(AccountModel)).all()
    platforms: dict = {}
    statuses: dict = {}
    for acc in accounts:
        platforms[acc.platform] = platforms.get(acc.platform, 0) + 1
        statuses[acc.status] = statuses.get(acc.status, 0) + 1
    return {"total": len(accounts), "by_platform": platforms, "by_status": statuses}


@router.get("/export")
def export_accounts(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    email: Optional[str] = None,
    session: Session = Depends(get_session),
):
    q = _apply_account_order(
        _apply_account_filters(
            select(AccountModel),
            platform=platform,
            status=status,
            email=email,
        )
    )
    accounts = session.exec(q).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["platform", "email", "password", "user_id", "region",
                     "status", "cashier_url", "created_at"])
    for acc in accounts:
        writer.writerow([acc.platform, acc.email, acc.password, acc.user_id,
                         acc.region, acc.status, acc.cashier_url,
                         acc.created_at.strftime("%Y-%m-%d %H:%M:%S")])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=accounts.csv"}
    )


@router.post("/import")
def import_accounts(
    body: ImportRequest,
    session: Session = Depends(get_session),
):
    """批量导入，每行格式: email password [extra]"""
    created = 0
    for line in body.lines:
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        email, password = parts[0], parts[1]
        extra = parts[2] if len(parts) > 2 else ""
        if extra:
            try:
                json.loads(extra)
            except (json.JSONDecodeError, ValueError):
                extra = "{}"
        else:
            extra = "{}"
        acc = AccountModel(platform=body.platform, email=email,
                           password=password, extra_json=extra)
        session.add(acc)
        created += 1
    session.commit()
    return {"created": created}


@router.post("/batch-delete")
def batch_delete_accounts(
    body: BatchDeleteRequest,
    session: Session = Depends(get_session)
):
    """批量删除账号"""
    deleted_count = 0
    not_found_ids = []

    try:
        if body.ids:
            unique_ids = []
            seen = set()
            for raw in body.ids:
                account_id = int(raw)
                if account_id <= 0 or account_id in seen:
                    continue
                seen.add(account_id)
                unique_ids.append(account_id)

            if not unique_ids:
                raise HTTPException(400, "账号 ID 列表不能为空")

            for account_id in unique_ids:
                acc = session.get(AccountModel, account_id)
                if acc:
                    session.delete(acc)
                    deleted_count += 1
                else:
                    not_found_ids.append(account_id)
        else:
            if not body.all_filtered:
                raise HTTPException(400, "请提供账号 ID 列表，或指定 all_filtered=true")
            if not body.status and not body.email:
                raise HTTPException(400, "批量删除当前筛选结果时，至少需要提供 status 或 email 条件")

            query = select(AccountModel)
            query = _apply_account_filters(
                query,
                platform=body.platform,
                status=body.status,
                email=body.email,
            )
            rows = session.exec(query).all()
            for acc in rows:
                session.delete(acc)
                deleted_count += 1

        session.commit()
        logger.info(f"批量删除成功: {deleted_count} 个账号")

        return {
            "deleted": deleted_count,
            "not_found": not_found_ids,
            "total_requested": len(body.ids) if body.ids else deleted_count,
        }
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.exception("批量删除失败")
        raise HTTPException(500, f"批量删除失败: {str(e)}")


@router.post("/batch-upload-sub2api")
def batch_upload_sub2api(
    body: BatchSub2ApiUploadRequest,
    session: Session = Depends(get_session),
):
    if not body.ids:
        raise HTTPException(400, "账号 ID 列表不能为空")

    unique_ids = list(dict.fromkeys(body.ids))
    if len(unique_ids) > 1000:
        raise HTTPException(400, "单次最多上传 1000 个账号")

    results = {
        "success_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "details": [],
    }

    upload_candidates = []
    valid_accounts = []

    for account_id in unique_ids:
        acc = session.get(AccountModel, account_id)
        if not acc:
            results["failed_count"] += 1
            results["details"].append({"id": account_id, "email": None, "success": False, "error": "账号不存在"})
            continue
        if acc.platform != "chatgpt":
            results["skipped_count"] += 1
            results["details"].append({"id": account_id, "email": acc.email, "success": False, "error": "仅支持上传 ChatGPT 账号"})
            continue

        upload_account = _to_chatgpt_upload_account(acc)
        if not upload_account.access_token:
            results["skipped_count"] += 1
            results["details"].append({"id": account_id, "email": acc.email, "success": False, "error": "缺少 access_token"})
            continue

        valid_accounts.append(acc)
        upload_candidates.append(upload_account)

    if not upload_candidates:
        return results

    from platforms.chatgpt.sub2api_upload import upload_to_sub2api
    from services.chatgpt_sync import update_account_model_sub2api_sync
    from services.sub2api_sync import sync_chatgpt_sub2api_status_batch

    success, message = upload_to_sub2api(upload_candidates, group_ids=body.group_ids)
    if success:
        sync_results = sync_chatgpt_sub2api_status_batch(valid_accounts)
        for acc in valid_accounts:
            sync_result = sync_results.get(int(acc.id or 0), {})
            if sync_result:
                update_account_model_sub2api_sync(acc, sync_result, session=session, commit=False)
            results["success_count"] += 1
            results["details"].append({"id": acc.id, "email": acc.email, "success": True, "message": message})
        session.commit()
    else:
        for acc in valid_accounts:
            results["failed_count"] += 1
            results["details"].append({"id": acc.id, "email": acc.email, "success": False, "error": message})

    return results


@router.post("/check-all")
def check_all_accounts(platform: Optional[str] = None,
                       background_tasks: BackgroundTasks = None):
    from core.scheduler import scheduler
    background_tasks.add_task(scheduler.check_accounts_valid, platform)
    return {"message": "批量检测任务已启动"}


@router.get("/{account_id}")
def get_account(account_id: int, session: Session = Depends(get_session)):
    acc = session.get(AccountModel, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    return acc


@router.patch("/{account_id}")
def update_account(account_id: int, body: AccountUpdate,
                   session: Session = Depends(get_session)):
    acc = session.get(AccountModel, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    if body.status is not None:
        acc.status = body.status
    if body.token is not None:
        acc.token = body.token
    if body.cashier_url is not None:
        acc.cashier_url = body.cashier_url
    acc.updated_at = datetime.now(timezone.utc)
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


@router.delete("/{account_id}")
def delete_account(account_id: int, session: Session = Depends(get_session)):
    acc = session.get(AccountModel, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    session.delete(acc)
    session.commit()
    return {"ok": True}


@router.post("/{account_id}/check")
def check_account(account_id: int, background_tasks: BackgroundTasks,
                  session: Session = Depends(get_session)):
    acc = session.get(AccountModel, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    background_tasks.add_task(_do_check, account_id)
    return {"message": "检测任务已启动"}


def _do_check(account_id: int):
    from core.db import engine
    from sqlmodel import Session
    with Session(engine) as s:
        acc = s.get(AccountModel, account_id)
    if acc:
        from core.base_platform import Account, RegisterConfig
        from core.registry import get
        try:
            PlatformCls = get(acc.platform)
            plugin = PlatformCls(config=RegisterConfig())
            obj = Account(platform=acc.platform, email=acc.email,
                         password=acc.password, user_id=acc.user_id,
                         region=acc.region, token=acc.token,
                         extra=json.loads(acc.extra_json or "{}"))
            valid = plugin.check_valid(obj)
            with Session(engine) as s:
                a = s.get(AccountModel, account_id)
                if a:
                    if a.platform != "chatgpt":
                        a.status = a.status if valid else "invalid"
                    a.updated_at = datetime.now(timezone.utc)
                    s.add(a)
                    s.commit()
        except Exception:
            logger.exception("检测账号 %s 时出错", account_id)
