"""设置：Hyonix 账号、常规参数、修改后台密码。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlmodel import Session, select

from ..config import settings as app_settings
from ..db import engine, get_bool_setting, get_setting, set_setting
from ..deps import require_user
from ..models import MonitorTask, User
from ..security import hash_password, verify_password
from ..templating import flash, render
from ..services import scheduler as sched
from ..services.hyonix_session import LoginError, credentials, session as hyonix_session

router = APIRouter()



@router.get("/settings")
def settings_page(request: Request, user=Depends(require_user)):
    general = {
        "display_tz": get_setting("display_tz") or "UTC",
        "catalog_sync_hours": get_setting("catalog_sync_hours") or "24",
        "min_interval_seconds": get_setting("min_interval_seconds") or "15",
    }
    email, password = credentials()
    fail_png = app_settings.data_dir / "login_fail.png"
    hyonix = {
        "email": email, "password": password,
        "logged_in": hyonix_session.has_session,
        "error": hyonix_session.last_error,
        # 有登录错误且有现场截图时展示（时间戳用于避免浏览器缓存旧图）
        "fail_shot": int(fail_png.stat().st_mtime) if hyonix_session.last_error and fail_png.exists() else 0,
    }
    return render(request, "settings.html", user=user, general=general, hyonix=hyonix,
                  notify_invalid=get_bool_setting("notify_target_invalid", True))


@router.post("/settings/hyonix")
async def hyonix_account_save(request: Request, user=Depends(require_user),
                              email: str = Form(""), password: str = Form("")):
    """保存 Hyonix 账号并立即测试登录：清除旧会话，绕过失败冷却，成功后下一轮轮询即用新账号。"""
    email = email.strip()
    if not email or not password:
        flash(request, "邮箱和密码都不能为空", "error")
        return RedirectResponse("/settings#hyonix", status_code=303)
    set_setting("hyonix_email", email)
    set_setting("hyonix_password", password)
    hyonix_session.clear()
    try:
        await hyonix_session.login(force=True)
    except LoginError as e:
        flash(request, f"账号已保存，但登录失败：{e}", "error")
    else:
        flash(request, "账号已保存，登录成功，监控将使用新账号继续运行", "success")
    return RedirectResponse("/settings#hyonix", status_code=303)


@router.get("/settings/hyonix/login-fail.png")
def hyonix_login_fail_shot(user=Depends(require_user)):
    path = app_settings.data_dir / "login_fail.png"
    if not path.exists():
        return Response(status_code=404)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/settings")
async def settings_save(request: Request, user=Depends(require_user)):
    form = await request.form()
    set_setting("display_tz", (form.get("display_tz") or "UTC").strip())
    set_setting("catalog_sync_hours", str(form.get("catalog_sync_hours") or "24").strip())
    set_setting("min_interval_seconds", str(form.get("min_interval_seconds") or "15").strip())
    # 日志记录规则已移至日志页「记录设置」（POST /logs/settings），此处不再处理
    set_setting("notify_target_invalid", "1" if form.get("notify_target_invalid") == "on" else "0")
    # 应用新的同步间隔与最小轮询间隔
    sched.schedule_global_sync()
    with Session(engine) as s:
        for t in s.exec(select(MonitorTask).where(MonitorTask.enabled == True)).all():  # noqa: E712
            sched.add_task_job(t, run_soon=False)
    flash(request, "设置已保存", "success")
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/account")
def change_account(request: Request, user=Depends(require_user),
                   username: str = Form(...), old_password: str = Form(...),
                   new_password: str = Form(""), confirm_password: str = Form("")):
    """修改用户名和/或密码；新密码留空表示不修改。均需验证当前密码。"""
    username = username.strip()
    if not username:
        flash(request, "用户名不能为空", "error")
        return RedirectResponse("/settings", status_code=303)
    if new_password or confirm_password:
        if new_password != confirm_password:
            flash(request, "两次新密码不一致", "error")
            return RedirectResponse("/settings", status_code=303)
        if len(new_password) < 6:
            flash(request, "新密码至少 6 位", "error")
            return RedirectResponse("/settings", status_code=303)
    with Session(engine) as s:
        u = s.get(User, user.id)
        if not u or not verify_password(old_password, u.password_hash):
            flash(request, "当前密码错误", "error")
            return RedirectResponse("/settings", status_code=303)
        changed = []
        if username != u.username:
            if s.exec(select(User).where(User.username == username, User.id != u.id)).first():
                flash(request, f"用户名「{username}」已被占用", "error")
                return RedirectResponse("/settings", status_code=303)
            u.username = username
            changed.append("用户名")
        if new_password:
            u.password_hash = hash_password(new_password)
            changed.append("密码")
        if not changed:
            flash(request, "没有需要保存的修改", "info")
            return RedirectResponse("/settings", status_code=303)
        s.add(u)
        s.commit()
    flash(request, f"已更新{'和'.join(changed)}，下次登录请使用新的凭证", "success")
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/password")
def change_password(request: Request, user=Depends(require_user),
                    old_password: str = Form(...), new_password: str = Form(...),
                    confirm_password: str = Form(...)):
    if new_password != confirm_password:
        flash(request, "两次新密码不一致", "error")
        return RedirectResponse("/settings", status_code=303)
    if len(new_password) < 6:
        flash(request, "新密码至少 6 位", "error")
        return RedirectResponse("/settings", status_code=303)
    with Session(engine) as s:
        u = s.get(User, user.id)
        if not u or not verify_password(old_password, u.password_hash):
            flash(request, "原密码错误", "error")
            return RedirectResponse("/settings", status_code=303)
        u.password_hash = hash_password(new_password)
        s.add(u)
        s.commit()
    flash(request, "密码已修改", "success")
    return RedirectResponse("/settings", status_code=303)
