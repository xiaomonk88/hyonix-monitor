"""全局配置：从环境变量 / .env 读取，提供带默认值的强类型访问。"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（app/ 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent

# 加载 .env（若存在）
load_dotenv(BASE_DIR / ".env")


def _get(key: str, default: str) -> str:
    val = os.getenv(key)
    return val if val not in (None, "") else default


class Settings:
    # 会话密钥
    secret_key: str = _get("APP_SECRET_KEY", "") or secrets.token_hex(32)

    # 时区（展示用）
    display_tz: str = _get("DISPLAY_TZ", "Asia/Shanghai")


    # 数据目录
    data_dir: Path = Path(_get("DATA_DIR", str(BASE_DIR / "data")))

    # 调度
    catalog_sync_hours: float = float(_get("CATALOG_SYNC_HOURS", "1"))
    min_interval_seconds: int = int(_get("MIN_INTERVAL_SECONDS", "20"))

    # Hyonix 站点与登录账号（浏览器自动登录用）
    hyonix_base: str = _get("HYONIX_BASE", "https://my.hyonix.com").rstrip("/")
    hyonix_email: str = _get("HYONIX_EMAIL", "")
    hyonix_password: str = _get("HYONIX_PASSWORD", "")
    # 登录用浏览器：空 = 自动（装了 Google Chrome 用 chrome，否则 patchright 自带 Chromium）；
    # 本机调试可设为 chrome / msedge
    browser_channel: str = _get("BROWSER_CHANNEL", "") or (
        "chrome" if Path("/opt/google/chrome/chrome").exists() else "")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "hyonix.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
