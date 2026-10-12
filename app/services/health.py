"""站点抓取健康状态：连续失败告警 + 恢复提醒（推送到所有启用渠道）。

避免「监控已失效却以为一直缺货」：登录失败、Cloudflare 拦截、页面结构变化等
连续 FAIL_THRESHOLD 次后推送一次告警；之后首次成功再推送一次恢复提醒。
状态存于设置表（site_health_*），重启后不会重复告警。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlmodel import Session, select

from ..db import engine, get_setting, set_setting
from ..models import Channel

log = logging.getLogger("hyonix.health")

FAIL_THRESHOLD = 3
_fail_count = 0


def _now_text() -> str:
    return f"{datetime.now(timezone.utc).astimezone():%Y-%m-%d %H:%M:%S}"


async def _broadcast(text: str) -> None:
    from .notifier import notify_channels
    with Session(engine) as s:
        ids = [c.id for c in s.exec(select(Channel).where(Channel.enabled == True)).all()]  # noqa: E712
    if ids:
        await notify_channels(ids, text)


async def report_failure(err: Exception) -> None:
    global _fail_count
    _fail_count += 1
    msg = str(err) or err.__class__.__name__
    set_setting("site_health_error", msg[:300])
    set_setting("site_health_error_at", datetime.now(timezone.utc).isoformat())
    if _fail_count >= FAIL_THRESHOLD and get_setting("site_health_alerted") != "1":
        set_setting("site_health_alerted", "1")
        log.warning("站点抓取连续失败 %d 次，推送告警：%s", _fail_count, msg)
        await _broadcast(f"⚠️ Hyonix 监控异常\n\n连续 {_fail_count} 次抓取失败，补货提醒暂时无法送达。\n"
                         f"原因：{msg}\n\n系统会继续自动重试（含自动重新登录），恢复后会再通知。\n🕐 {_now_text()}")


async def report_ok() -> None:
    global _fail_count
    _fail_count = 0
    if get_setting("site_health_alerted") == "1":
        set_setting("site_health_alerted", "0")
        set_setting("site_health_error", "")
        await _broadcast(f"✅ Hyonix 监控已恢复\n\n抓取恢复正常，补货提醒继续推送。\n🕐 {_now_text()}")
