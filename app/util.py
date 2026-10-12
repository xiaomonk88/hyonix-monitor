"""通用工具：区域/规格 slug 规范化与下单链接。

Hyonix：机房 slug 为小写机房代码（phx1），套餐 slug 为小写套餐名（hs-1）。
订购页是单页向导，不支持预选机房/套餐的深链，下单链接统一指向 order.php。
"""
from __future__ import annotations

from .config import settings


def canon_region(region: str) -> str:
    r = (region or "").strip()
    return r if r == "*" else r.lower()


def canon_slug(slug: str) -> str:
    return (slug or "").strip().lower()


def order_url(region: str, slug: str) -> str:
    return f"{settings.hyonix_base}/order.php"


# 大洲英文 -> 中文
CONTINENT_CN = {
    "Asia": "亚洲", "Europe": "欧洲", "North America": "北美",
    "South America": "南美", "Africa": "非洲", "Oceania": "大洋洲", "": "其他",
}


def continent_cn(name: str) -> str:
    return CONTINENT_CN.get(name or "", name or "其他")


def fmt_price(p) -> str:
    try:
        v = float(p or 0)
    except (TypeError, ValueError):
        return "-"
    return f"${v:.0f}/月" if abs(v - round(v)) < 1e-9 else f"${v:.2f}/月"


def fmt_usd(p) -> str:
    """金额数字：整数不带小数，否则保留两位（6 / 6.50）。"""
    try:
        v = float(p or 0)
    except (TypeError, ValueError):
        return "-"
    return f"{v:.0f}" if abs(v - round(v)) < 1e-9 else f"{v:.2f}"


def fmt_mem(mib) -> str:
    mib = int(mib or 0)
    if not mib:
        return "-"
    if mib < 1024:
        return f"{mib} MB"
    g = mib / 1024
    return f"{g:.0f} GB" if abs(g - round(g)) < 1e-9 else f"{g:.1f} GB"


def fmt_gb(mib) -> str:
    mib = int(mib or 0)
    if not mib:
        return "-"
    g = mib / 1024
    return f"{g:.0f} GB" if abs(g - round(g)) < 1e-9 else f"{g:.1f} GB"


def fmt_transfer(gb) -> str:
    """月流量（单位 GB）：0 表示不限流量；不足 1 TB 显示 GB，否则显示 TB。"""
    gb = int(gb or 0)
    if not gb:
        return "不限"
    if gb < 1024:
        return f"{gb} GB"
    t = gb / 1024
    return f"{t:.0f} TB" if abs(t - round(t)) < 0.05 else f"{t:.1f} TB"


def fmt_specs(vcores, memory, disk, transfer) -> str:
    """紧凑规格串：1C / 2 GB / 31 GB / 流量 不限"""
    parts = []
    if vcores:
        parts.append(f"{int(vcores)}C")
    for v, f in ((memory, fmt_mem), (disk, fmt_gb)):
        if v:
            parts.append(f(v))
    if vcores:
        parts.append(f"流量 {fmt_transfer(transfer)}")
    return " / ".join(parts)
