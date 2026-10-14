"""Hyonix（WHMCS）登录会话：浏览器自动登录 + HTTPX 轻量抓取。

站点特性（2026-10 实测）：
- clientarea.php / dologin.php 有 Cloudflare Turnstile 质询，HTTP 客户端与普通无头浏览器都无法通过；
- order.php 等页面不受质询，登录后用 HTTP 带 Cookie 抓取即可。

设计要点：
- 登录走 patchright（去除自动化特征的 Playwright）+ 有界面 Chromium；Docker 中由 Xvfb 提供虚拟显示器；
- 登录成功后把 Cookie 与 UA 交给 HTTPX，日常轮询不启动浏览器；勾选 Remember Me，会话长期有效；
- 每次抓取检测是否掉线（被重定向到登录页 / 出现登录表单），掉线自动重新登录并重试一次；
- Cookie 持久化到 data/hyonix_cookies.json，容器重启无需重新登录；
- 登录失败后进入冷却期，避免反复提交密码触发风控；失败现场保存到 data/login_fail.*。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

import httpx

from ..config import settings
from ..db import get_setting

log = logging.getLogger("hyonix.session")

_FALLBACK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
_LOGIN_COOLDOWN = 300.0     # 登录失败后的冷却时间（秒）
_CHALLENGE_TIMEOUT = 60.0   # 等待 Cloudflare 质询通过的最长时间（秒）


class LoginError(RuntimeError):
    """登录失败（账号密码错误 / 质询未通过 / 页面结构变化等）。"""


class FetchError(RuntimeError):
    """抓取失败（网络、Cloudflare 拦截、HTTP 错误等）。"""


def _is_login_page(html: str, url: str) -> bool:
    return ("dologin.php" in html and 'name="password"' in html) or "/login" in url.lower()


def _is_cf_challenge(status: int, html: str) -> bool:
    return status in (403, 503) and ("cf-chl" in html or "challenge-platform" in html
                                      or "Just a moment" in html)


def credentials() -> tuple[str, str]:
    """Hyonix 账号：后台设置优先，未设置时用 .env 的 HYONIX_EMAIL / HYONIX_PASSWORD。每次登录时现读。"""
    email = (get_setting("hyonix_email") or "").strip() or settings.hyonix_email
    password = get_setting("hyonix_password") or settings.hyonix_password
    return email, password


def _site_cookies(cookies: list[dict]) -> dict[str, str]:
    return {c["name"]: c["value"] for c in cookies if c["domain"].lstrip(".").endswith("hyonix.com")}


class HyonixSession:
    def __init__(self) -> None:
        self.base = settings.hyonix_base
        self.cookie_file = settings.data_dir / "hyonix_cookies.json"
        self._cookies: dict[str, str] = {}
        self._ua = _FALLBACK_UA
        self._login_lock = asyncio.Lock()
        self._last_login_fail = 0.0
        self.last_login_at: Optional[float] = None
        self.last_error: str = ""
        self._load_cookies()

    # ---- Cookie 持久化 ----------------------------------------------------
    def _load_cookies(self) -> None:
        try:
            data = json.loads(self.cookie_file.read_text("utf-8"))
            self._cookies = data.get("cookies") or {}
            self._ua = data.get("ua") or _FALLBACK_UA
            self.last_login_at = data.get("login_at")
        except (OSError, ValueError):
            self._cookies = {}

    def _save_cookies(self) -> None:
        self.cookie_file.write_text(json.dumps(
            {"cookies": self._cookies, "ua": self._ua, "login_at": self.last_login_at}), "utf-8")

    def clear(self) -> None:
        self._cookies = {}
        self.cookie_file.unlink(missing_ok=True)

    @property
    def has_session(self) -> bool:
        return bool(self._cookies)

    def url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.base}/{path.lstrip('/')}"

    # ---- 浏览器登录 -------------------------------------------------------
    async def login(self, force: bool = False) -> None:
        email, password = credentials()
        if not email or not password:
            raise LoginError("未配置 Hyonix 账号（在「设置」页填写）")
        async with self._login_lock:
            remain = _LOGIN_COOLDOWN - (time.monotonic() - self._last_login_fail)
            if not force and self._last_login_fail and remain > 0:
                raise LoginError(f"登录冷却中（{int(remain)} 秒后重试）：{self.last_error}")
            try:
                await self._browser_login(email, password)
            except Exception as e:  # noqa: BLE001
                self._last_login_fail = time.monotonic()
                self.last_error = (str(e) or e.__class__.__name__).splitlines()[0]
                log.warning("Hyonix 登录失败：%s", self.last_error)
                raise LoginError(self.last_error) from e
            self._last_login_fail = 0.0
            self.last_error = ""
            self.last_login_at = time.time()
            self._save_cookies()
            log.info("Hyonix 登录成功")

    async def _browser_login(self, email: str, password: str) -> None:
        from patchright.async_api import async_playwright

        async with async_playwright() as pw:
            # 有界面模式：无头模式会被 Turnstile 识别。Docker 中由 Xvfb 提供显示器
            browser = await pw.chromium.launch(
                headless=False, channel=settings.browser_channel or None,
                args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                # 保持默认上下文：自定义 viewport / locale 等指纹会导致 Turnstile 不放行（实测）
                ctx = await browser.new_context()
                page = await ctx.new_page()
                try:
                    await self._submit_login(page, email, password)
                except Exception:
                    await self._dump_failure(page)
                    raise
                self._ua = await page.evaluate("navigator.userAgent")
                self._cookies = _site_cookies(await ctx.cookies())
            finally:
                await browser.close()

    async def _wait_login_form(self, page) -> None:
        """等待 Cloudflare 质询通过、登录框出现；久未通过时尝试点击 Turnstile 勾选框。"""
        deadline = time.monotonic() + _CHALLENGE_TIMEOUT
        clicked = False
        while time.monotonic() < deadline:
            if await page.locator('input[name="password"]').count():
                return
            if not clicked and time.monotonic() > deadline - _CHALLENGE_TIMEOUT / 2:
                # Turnstile 在封闭 shadow root 的 iframe 中，按其宿主元素坐标点击勾选框
                box = await page.evaluate("""() => {
                    const i = document.querySelector('input[name="cf-turnstile-response"]');
                    if (!i) return null; const r = i.parentElement.getBoundingClientRect();
                    return {x: r.x, y: r.y, h: r.height};}""")
                if box and box["h"]:
                    await page.mouse.click(box["x"] + 30, box["y"] + box["h"] / 2)
                    clicked = True
            await asyncio.sleep(1.5)
        raise LoginError("Cloudflare 质询未通过（超时）")

    async def _submit_login(self, page, email: str, password: str) -> None:
        await page.goto(self.url("clientarea.php"), wait_until="domcontentloaded", timeout=60000)
        await self._wait_login_form(page)
        await page.fill('input[name="username"]', email)
        await page.fill('input[name="password"]', password)
        # Remember Me 是自定义样式复选框（原生 input 隐藏），直接置位
        await page.evaluate("""() => { const r = document.querySelector('input[name="rememberme"]');
                                       if (r) r.checked = true; }""")
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=60000):
            await page.click('#login, button[type="submit"], input[type="submit"]')
        if _is_login_page(await page.content(), page.url):
            # Hyonix 模板的登录错误是 .alert-toast-danger；只取可见元素（页面里隐藏的「生成密码」弹窗也带 .alert-danger）
            alert = page.locator(".alert-toast-danger:visible, .alert-danger:visible")
            msg = " ".join((await alert.first.inner_text()).split()) if await alert.count() else ""
            if "incorrect" in msg.lower():
                msg = f"账号或密码错误（{msg}）"
            raise LoginError(msg or "提交后仍停留在登录页（账号或密码错误？）")

    async def _dump_failure(self, page) -> None:
        try:
            await page.screenshot(path=str(settings.data_dir / "login_fail.png"), full_page=True)
            (settings.data_dir / "login_fail.html").write_text(await page.content(), "utf-8")
        except Exception:  # noqa: BLE001
            pass

    # ---- 抓取 -------------------------------------------------------------
    async def _http_get(self, url: str) -> tuple[int, str, str]:
        headers = {"User-Agent": self._ua, "Accept": "text/html,application/xhtml+xml",
                   "Accept-Language": "en-US,en;q=0.9"}
        async with httpx.AsyncClient(timeout=30, headers=headers, cookies=self._cookies,
                                     follow_redirects=True) as c:
            r = await c.get(url)
            self._cookies.update(dict(c.cookies.items()))  # 服务端轮换的会话 Cookie
            return r.status_code, r.text, str(r.url)

    async def get(self, path: str) -> str:
        """抓取需要登录的页面，返回 HTML。掉线自动重新登录并重试一次。"""
        url = self.url(path)
        if not self.has_session:
            await self.login()
        for attempt in range(2):
            try:
                status, html, final = await self._http_get(url)
            except httpx.HTTPError as e:
                raise FetchError(f"网络错误：{e.__class__.__name__}") from e
            if _is_cf_challenge(status, html):
                raise FetchError("被 Cloudflare 质询拦截")
            if _is_login_page(html, final):
                if attempt == 0:
                    log.info("会话失效，重新登录")
                    await self.login()
                    continue
                raise LoginError("重新登录后仍被重定向到登录页")
            if status >= 400:
                raise FetchError(f"HTTP {status}")
            self._save_cookies()
            return html
        raise FetchError("unreachable")


session = HyonixSession()
