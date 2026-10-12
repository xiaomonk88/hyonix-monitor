"""Hyonix 库存数据客户端（接口与原 misaka_client 保持一致，供目录同步与任务轮询复用）。

数据源：登录后的 https://my.hyonix.com/order.php，页面内嵌两段 JSON：
  window.allLocations = {code: {Code, FirstName(州/国), LastName(城市), Enabled, Locked,
                                "sold-out", plans: [pid, ...], servers: {...}, FreeIPs}}
  window.allPlans     = {pid: {id, gid, name, hidden, retired, configoption1(vCPU),
                                configoption2(NVMe GB), configoption5(RAM MiB), price{currency: {...}}}}
有货判定与前端 getPlans() 一致：pid ∈ allLocations[loc].plans。
plans 中 pid 的重复次数 = 能开出该套餐的宿主机数，作为「余量」参考。
一次抓取即得全站数据，多个任务共用短期缓存。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter
from typing import Any

from .hyonix_session import FetchError, session

_CACHE_TTL = 4.0  # 秒
_VAR_RE = r"window\.{}\s*=\s*"
_GROUP_RE = re.compile(r'class="[^"]*server-type[^"]*"\s+data-gid="(\d+)".*?id="server-name">([^<]+)<', re.S)

# 机房代码前缀 -> 大洲（新机房未收录时归入「其他」）
_CONTINENT = {
    "lax": "North America", "las": "North America", "phx": "North America", "sea": "North America",
    "pdx": "North America", "slc": "North America", "chi": "North America", "dal": "North America",
    "clt": "North America", "pit": "North America", "iad": "North America", "nyc": "North America",
    "mia": "North America", "tor": "North America", "atl": "North America", "den": "North America",
    "lon": "Europe", "ams": "Europe", "fra": "Europe", "dub": "Europe", "par": "Europe",
    "sin": "Asia", "hkg": "Asia", "tyo": "Asia", "syd": "Oceania",
}
_US_STATES = {
    "California", "Nevada", "Arizona", "Washington", "Oregon", "Utah", "Illinois", "Texas",
    "North Carolina", "Pennsylvania", "Virginia", "New York", "New Jersey", "Florida", "Georgia", "Colorado",
}


def _extract(html: str, name: str) -> Any:
    m = re.search(_VAR_RE.format(name), html)
    if not m:
        raise FetchError(f"页面中未找到 window.{name}（页面结构可能已变更）")
    return json.JSONDecoder().raw_decode(html, m.end())[0]


def _location_dict(code: str, loc: dict[str, Any]) -> dict[str, Any]:
    state = loc.get("FirstName", "")
    country = "United States" if state in _US_STATES else state
    return {
        "slug": code,
        "name": loc.get("LastName", "") or code,
        "country": country,
        "country_code": "",
        "continent": _CONTINENT.get(re.sub(r"\d+$", "", code), ""),
        "available": bool(loc.get("Enabled")) and not loc.get("Locked") and not loc.get("sold-out"),
        "raw": {k: loc.get(k) for k in ("id", "Code", "Notes", "FirstName", "LastName", "Enabled",
                                         "Locked", "sold-out", "FreeIPs")},
    }


def _plan_dict(plan: dict[str, Any], region_ok: bool, hosts: int) -> dict[str, Any]:
    price = (plan.get("price") or {}).get("1") or {}

    def num(v) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    available = region_ok and hosts > 0
    return {
        "slug": (plan.get("name") or str(plan.get("id"))).strip().lower(),
        "name": plan.get("name", ""),
        "pid": plan.get("id"),
        "gid": plan.get("gid"),
        "vcores": int(num(plan.get("configoption1"))),
        "memory": int(num(plan.get("configoption5"))),          # MiB
        "disk": int(num(plan.get("configoption2")) * 1024),    # GB -> MiB
        "transfer": 0,                                          # 全部不限流量
        "nvme": True,
        "price_monthly": num(price.get("monthly")),
        "price": {k: num(price.get(k)) for k in ("monthly", "quarterly", "semiannually", "annually")},
        "available": available,
        "unavailable_reason": "" if available else "out_of_stock",
        "hosts": hosts,
    }


class HyonixClient:
    def __init__(self) -> None:
        self._cache: tuple[float, list[dict[str, Any]], dict[str, list[dict[str, Any]]]] | None = None
        self._lock = asyncio.Lock()
        self.group_names: dict[int, str] = {}

    async def _snapshot(self, use_cache: bool = True):
        from . import health
        try:
            result = await self._snapshot_locked(use_cache)
        except Exception as e:
            await health.report_failure(e)
            raise
        await health.report_ok()
        return result

    async def _snapshot_locked(self, use_cache: bool):
        async with self._lock:
            now = time.monotonic()
            if use_cache and self._cache and now - self._cache[0] < _CACHE_TTL:
                return self._cache[1], self._cache[2]
            html = await session.get("order.php")
            locs = _extract(html, "allLocations")
            plans = _extract(html, "allPlans")
            # 产品线名称（Choose Server 步骤）：取页面中 gid 对应的按钮文字，取不到用 gid
            for gid, label in _GROUP_RE.findall(html):
                self.group_names[int(gid)] = label.strip()
            visible = [p for p in plans.values() if not p.get("hidden") and not p.get("retired")]
            regions, by_region = [], {}
            for code, loc in locs.items():
                r = _location_dict(code, loc)
                counts = Counter(loc.get("plans") or [])
                regions.append(r)
                by_region[code] = [
                    {**_plan_dict(p, r["available"], counts.get(p["id"], 0)),
                     "tags": [self.group_names.get(p.get("gid"), "Standard")]}
                    for p in visible
                ]
            self._cache = (now, regions, by_region)
            return regions, by_region

    async def fetch_regions(self) -> list[dict[str, Any]]:
        return (await self._snapshot())[0]

    async def fetch_region_plans(self, region: str, use_cache: bool = True) -> list[dict[str, Any]]:
        _, by_region = await self._snapshot(use_cache)
        return by_region.get(region, [])

    async def fetch_all(self) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        """全量抓取：返回 (regions, {region_slug: [plans]})。"""
        return await self._snapshot(use_cache=False)


client = HyonixClient()
