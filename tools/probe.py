"""开发用探测脚本：自动登录后保存订购页及其链接到的产品组页面，供编写解析器参考。

用法（项目根目录）：.venv/Scripts/python tools/probe.py
输出：data/probe/*.html 与 data/probe/index.txt
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services.hyonix_session import session  # noqa: E402

LINK_RE = re.compile(r'href="([^"]*(?:gid=|pid=|cart\.php|store/|order\.php)[^"]*)"', re.I)


async def main() -> None:
    out = settings.data_dir / "probe"
    out.mkdir(parents=True, exist_ok=True)
    await session.login(force=True)
    queue, seen, index = ["order.php"], set(), []
    while queue and len(seen) < 40:
        path = queue.pop(0).replace("&amp;", "&")
        if path in seen:
            continue
        seen.add(path)
        try:
            html = await session.get(path)
        except Exception as e:  # noqa: BLE001
            index.append(f"FAIL {path} {e}")
            continue
        name = re.sub(r"[^\w.-]+", "_", path)[:80] + ".html"
        (out / name).write_text(html, "utf-8")
        index.append(f"OK   {path} -> {name} ({len(html)} bytes)")
        print(index[-1])
        for href in LINK_RE.findall(html):
            full = urljoin(settings.hyonix_base + "/", href.replace("&amp;", "&"))
            if full.startswith(settings.hyonix_base) and "a=add" not in full and "logout" not in full:
                queue.append(full[len(settings.hyonix_base) + 1:])
        await asyncio.sleep(1)
    (out / "index.txt").write_text("\n".join(index), "utf-8")


if __name__ == "__main__":
    asyncio.run(main())
