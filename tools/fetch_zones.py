"""抓全量 USPS Zone Chart，做成离线查询表。

为什么自己做而不是让浏览器直接调 USPS 接口
-------------------------------------------
1. `postcalc.usps.com` 不返回 `Access-Control-Allow-Origin`，浏览器跨域会被拦。
2. 那个 GetZone 接口对 APO/FPO 返回 "Zone 1"，是错的 —— 官方自己说 APO 要
   用另一张表。直接信它会给出错误报价，而且错得静默。

数据结构
--------
官方每个起点返回一张表，按目的地 ZIP3 分段：

    Column0..Column3   同一张长表分四栏排版（不是四种服务）
    Zip5Digit          真正的例外，带 MailService 字段

所以：
- `origins[zip3]` = 1000 个字符的字符串，下标就是目的地 ZIP3，
  值是 zone 数字，未分配的 ZIP3 用 `-`
- `exceptions[zip3]` = 例外区间，查询时优先于主表

实测验证过的边界（从 10001 出发）：
    96913 -> 8（主表是 9，被例外 96900-96938 覆盖）
    96940 -> 9（不在例外内，用主表）
    09012 -> 1（主表 1，但 Priority Mail / GA<16oz 是 5，见例外）
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "zones.json")
URL = ("https://postcalc.usps.com/DomesticZoneChart/GetZoneChart"
       "?ZIPCode3Digit={zip3}&ShippingDate={date}")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
COLS = ("Column0", "Column1", "Column2", "Column3")


def fetch(zip3: str, date: str, retries: int = 3) -> dict:
    url = URL.format(zip3=zip3, date=date)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as rsp:
                return json.loads(rsp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            last = exc
            time.sleep(1.5 + attempt * 2)
    raise RuntimeError(f"{zip3}: {last}")


def span(zc: str) -> tuple[int, int]:
    """把官方给的范围换算成 0-999 的 ZIP3 下标（只用于主表）。"""
    def conv(s: str) -> int:
        s = s.strip()
        return int(s) // 100 if len(s) >= 5 else int(s)

    parts = zc.split("---")
    if len(parts) == 2:
        return conv(parts[0]), conv(parts[1])
    v = conv(parts[0])
    return v, v


def span5(zc: str) -> tuple[int, int]:
    """例外表保留 5 位精度，不压缩到 ZIP3。

    969 这一段内部就不一致：96900-96938 是 Zone 8，96940 之后是 Zone 9。
    压成 ZIP3 就等于把 96940 也算成 8，错一个 zone 就是一档价差。
    """
    parts = zc.strip().split("---")
    if len(parts) == 2:
        return int(parts[0]), int(parts[1])
    v = int(parts[0])
    return v, v


def build_row(payload: dict) -> tuple[str, list[dict], str]:
    """把四栏拼回 1000 字符的查询字符串，同时抽出例外。"""
    cells = ["-"] * 1000
    flags: set[str] = set()
    for col in COLS:
        for r in payload.get(col) or []:
            lo, hi = span(r["ZipCodes"])
            zone = str(r["Zone"]).strip()
            # 后缀 `*` / `+` 是官方的排版标记（含义未在文档中说明），
            # 主值仍是那个数字。数字照常入表，标记单独记下来备查。
            digits = "".join(ch for ch in zone if ch.isdigit())
            if not digits:
                continue
            digit = digits[0]
            for i in range(max(0, lo), min(999, hi) + 1):
                cells[i] = digit
            if any(ch in zone for ch in "*+"):
                flags.add(zone)

    exceptions = []
    for r in payload.get("Zip5Digit") or []:
        lo5, hi5 = span5(r["ZipCodes"])
        zone = str(r["Zone"]).strip()
        if not zone.isdigit():
            continue
        exceptions.append({
            "lo": lo5,
            "hi": hi5,
            "zone": int(zone),
            "service": (r.get("MailService") or "").strip(),
        })
    # 无条件的在前（service 为空），带服务的在后
    exceptions.sort(key=lambda e: (e["service"] != "", e["lo"], e["hi"]))
    return "".join(cells), exceptions, ",".join(sorted(flags))


def lookup_zone(data: dict, origin_zip: str, dest_zip: str,
                service: str | None = None) -> int | None:
    """参考实现：给一对 5 位 ZIP，返回 Zone。前端 JS 必须与这里逐条一致。

    规则（按优先级）：
      1. service 为空的例外 —— 无条件覆盖主表（96900-96938 就是这种，
         主表标 9，实际是 8）
      2. 指定了 service 时，匹配该服务的例外 —— APO 的 Priority Mail
         和 Ground Advantage(<16oz) 是 Zone 5，其余是 Zone 1
      3. 都没有就用主表
    """
    o = origin_zip.strip()[:3]
    d = dest_zip.strip()[:3]
    if len(o) != 3 or len(d) != 3 or not o.isdigit() or not d.isdigit():
        return None
    row = (data.get("origins") or {}).get(o)
    if not row:
        return None
    exc = (data.get("exceptions") or {}).get(o) or []
    dest5 = int(dest_zip.strip()[:5]) if len(dest_zip.strip()) >= 5 else int(d) * 100

    for e in exc:
        if not e["service"] and e["lo"] <= dest5 <= e["hi"]:
            return e["zone"]
    if service:
        key = service.lower()
        for e in exc:
            if e["service"] and e["lo"] <= dest5 <= e["hi"] and key in e["service"].lower():
                return e["zone"]
    base = row[int(d)]
    return None if base == "-" else int(base)


def main(argv: list[str]) -> int:
    date = argv[1] if len(argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    print(f"抓取 Zone Chart（ShippingDate={date}）…", flush=True)

    origins: dict[str, str] = {}
    exceptions: dict[str, list] = {}
    flagged: dict[str, str] = {}
    errors: list[dict] = []
    effective = None

    for i in range(1000):
        zip3 = f"{i:03d}"
        try:
            payload = fetch(zip3, date)
        except RuntimeError as exc:
            errors.append({"zip3": zip3, "error": str(exc)})
            continue

        if payload.get("PageError"):
            # 未分配的 ZIP3：官方回 "No Zones found"，这是正常情况不是错误
            continue
        if not any(payload.get(c) for c in COLS):
            continue

        effective = effective or payload.get("EffectiveDate")
        row, exc, flag = build_row(payload)
        if row.count("-") == 1000:
            errors.append({"zip3": zip3, "error": "解析出全空行"})
            continue
        origins[zip3] = row
        if exc:
            exceptions[zip3] = exc
        if flag:
            flagged[zip3] = flag

        if (i + 1) % 100 == 0:
            print(f"  …{i + 1}/1000  已收 {len(origins)} 个起点", flush=True)
        time.sleep(0.12)

    if not origins:
        print("没有抓到任何 Zone 数据", file=sys.stderr)
        return 1

    out = {
        "meta": {
            "source": "USPS Domestic Zone Chart",
            "source_url": URL,
            "effective_date": effective,
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "shipping_date_used": date,
            "origins": len(origins),
            "exceptions": sum(len(v) for v in exceptions.values()),
            "errors": errors,
            "note": (
                "origins[zip3] 是 1000 字符的字符串，下标即目的地 ZIP3，"
                "'-' 表示该 ZIP3 未分配。查询时先查主表，再用 exceptions 覆盖。"
            ),
        },
        "origins": dict(sorted(origins.items())),
        "exceptions": dict(sorted(exceptions.items())),
        "flagged": dict(sorted(flagged.items())),
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)

    size = os.path.getsize(OUT)
    print(f"\n写入 {os.path.relpath(OUT, ROOT)} — {len(origins)} 个起点, "
          f"{size / 1024:.0f} KB, 生效日 {effective}, 失败 {len(errors)}")
    if errors:
        for e in errors[:5]:
            print(f"  FAIL {e['zip3']}: {e['error'][:80]}")
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
