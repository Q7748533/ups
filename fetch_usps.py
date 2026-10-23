# ::ILANG
# [TYPE:fetcher][PROJECT:parcelrate]
# ::STATE{@SELF, role:usps_fetcher, reads:.ilang/site.ilang, writes:data/usps.json}
# ::RULE{每个价格都带 source_url 和 effective_date⇒缺一个就不写进 JSON}
# ::RULE{抓取失败⇒保留旧文件并标记 stale 不许用旧值冒充新值}
# ::BOUNDARY{never:编价格 用估算填缺口 混用不同年份的费率表|scope:permanent}
"""Fetch USPS official price CSVs and parse them into structured JSON."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import siteilang
import archive

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
PRICES_BASE = "https://www.usps.com/business/prices/2026/"
# 生效日只在这里能读到，CSV 文件头给的是文件生成日。
PRICE_CHANGE_URL = "https://pe.usps.com/PriceChange/Index"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
WEIGHT_RE = re.compile(r"weight\s+not\s+over\s*\((ounces?|lbs?|pounds?)\)", re.I)
ZONE_RE = re.compile(r"^zone\s*(\d+)$", re.I)
NUM_RE = re.compile(r"^\$?\s*(-?\d+(?:\.\d+)?)$")


def log(msg: str) -> None:
    print(msg, flush=True)


def fetch(url: str, timeout: int = 30, retries: int = 2) -> str:
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as rsp:
                raw = rsp.read()
            return raw.decode("utf-8-sig", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(2 + attempt * 3)
    raise RuntimeError(f"fetch failed: {url} ({last})")


def num(cell: str):
    """Parse a money/weight cell. Returns float or None. Never guesses."""
    if cell is None:
        return None
    m = NUM_RE.match(cell.strip().replace(",", ""))
    return float(m.group(1)) if m else None


def find_date(cells) -> str | None:
    for c in cells:
        m = DATE_RE.search(c or "")
        if m:
            mm, dd, yy = m.groups()
            return f"{yy}-{int(mm):02d}-{int(dd):02d}"
    return None


def weight_label(raw: float, is_ounces: bool) -> str:
    if is_ounces:
        v = round(raw, 2)
        return f"{v:g} oz"
    return f"{raw:g} lb"


def _zone_cols(row) -> list[str]:
    return [z.group(1) for z in (ZONE_RE.match((c or "").strip()) for c in row) if z]


def _is_single_piece(row) -> bool:
    return any("single piece" in (c or "").lower() for c in row)


def parse_price_csv(text: str) -> dict:
    """Parse one USPS price CSV.

    Three coexisting shapes:
      1. flat-rate block     -> first cell contains "Flat Rate"
      2. weight x zone matrix -> "Weight Not Over (...)" header, optional zone header
                                 (which may sit on the SAME row, or be omitted entirely
                                 when the pound block reuses the ounce block's zones),
                                 then numeric data rows
      3. fee block           -> first cell contains "fee"
    """
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {"effective_date": None, "flat_rate": [], "matrix": None, "fees": [], "special": []}

    effective = find_date(rows[0])
    flat_rate: list[dict] = []
    special: list[dict] = []
    fees: list[dict] = []
    matrix_rows: list[dict] = []
    zone_cols: list[str] = []
    is_ounces = False
    single_col = False
    state = "idle"  # idle | pending_zone | active

    for row in rows[1:]:
        if not row or all(not (c or "").strip() for c in row):
            continue
        first = (row[0] or "").strip()
        low = first.lower()

        m = WEIGHT_RE.search(first)
        if m:
            is_ounces = m.group(1).lower().startswith("ounce")
            inline = _zone_cols(row)
            if inline:
                zone_cols, single_col, state = inline, False, "active"
            elif _is_single_piece(row):
                zone_cols, single_col, state = ["single"], True, "active"
            else:
                state = "pending_zone"
            continue

        if state == "pending_zone":
            zones = _zone_cols(row)
            if zones:
                zone_cols, single_col, state = zones, False, "active"
                continue
            if _is_single_piece(row):
                zone_cols, single_col, state = ["single"], True, "active"
                continue
            # pound block with no header: reuse the previous zone columns
            if num(first) is not None and zone_cols:
                state = "active"
            else:
                state = "idle"
                continue

        if state == "active":
            w = num(first)
            if w is not None:
                prices: dict[str, float] = {}
                for i, zone in enumerate(zone_cols):
                    if i + 1 < len(row):
                        p = num(row[i + 1])
                        if p is not None:
                            prices[zone] = p
                if prices:
                    matrix_rows.append(
                        {
                            "weight_raw": w,
                            "weight_lb": round(w / 16, 4) if is_ounces else w,
                            "weight_label": weight_label(w, is_ounces),
                            "zones": prices,
                        }
                    )
                    continue
            state = "idle"

        if "flat rate" in low:
            price = next((num(c) for c in row[1:] if num(c) is not None), None)
            if price is not None:
                flat_rate.append({"name": first, "price": price})
            continue

        if "fee" in low or "surcharge" in low:
            price = next((num(c) for c in reversed(row[1:]) if num(c) is not None), None)
            if price is not None:
                fees.append({"name": first, "price": price})
            continue

        # non-numeric size class with its own price row (e.g. Ground Advantage "Oversized")
        if first and num(first) is None:
            price = next((num(c) for c in row[1:] if num(c) is not None), None)
            if price is not None and len(first) < 40:
                special.append({"name": first, "price": price})
            continue

    matrix = None
    if matrix_rows:
        matrix = {
            "zone_columns": zone_cols,
            "single_price": single_col,
            "rows": matrix_rows,
        }
    return {
        # 这是文件头的日期，官方用它标「文件生成日」，不是生效日。
        # 2026 年就吃过这个亏：文件头写 9/14，真实生效日是 10/4，
        # 中间三周页面上标着一个并不存在的生效日。生效日只能从
        # Postal Explorer 的价格变更公告读，见 fetch_price_change()。
        "file_date": effective,
        "flat_rate": flat_rate,
        "matrix": matrix,
        "fees": fees,
        "special": special,
    }


def parse_extra_services(text: str) -> dict:
    """Extra services use `group title` rows followed by `,sub-item,,,,price` rows."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {"file_date": None, "items": []}

    effective = find_date(rows[0])
    items: list[dict] = []
    group = ""

    for row in rows[1:]:
        if not row or all(not (c or "").strip() for c in row):
            continue
        first = (row[0] or "").strip()
        rest = row[1:]
        price = next((num(c) for c in reversed(rest) if num(c) is not None), None)

        if first and price is None:
            group = first          # section heading, e.g. "Certificate of Mailing"
            continue

        if not first and price is not None:
            label = next((c.strip() for c in rest if c.strip() and num(c) is None), "")
            if label:
                items.append({"group": group, "name": label, "price": price})
            continue

        if first and price is not None:
            # a first-level service with its own price (e.g. "Certified Mail,,,,,5.55").
            # It is NOT a child of the preceding heading, so it must not inherit `group`.
            items.append({"group": first, "name": first, "price": price})
            continue

    return {"file_date": effective, "items": items}


def fetch_price_change(today: str | None = None) -> dict:
    """Read the currently effective price change from Postal Explorer.

    官方 CSV 的文件头日期是文件生成日，不是生效日 —— 2026 年 10 月这次
    文件头写 9/14，实际 10/4 才生效，差 20 天。生效日只出现在
    pe.usps.com/PriceChange/Index 的公告里，两种写法：

        Time-Limited 2026 Price Change
        Starting October 4, 2026 through January 17, 2027

        July 2026 Price Change
        Effective July 12, 2026

    返回生效日 <= 今天的最近一次变更。拿不到就报错，不猜日期 ——
    一个编出来的生效日比没有生效日更危险，因为它看起来是可信的。
    """
    html = fetch(PRICE_CHANGE_URL)
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)

    D = r"([A-Z][a-z]+ \d{1,2}, \d{4})"
    pat = re.compile(
        r"(Time-Limited\s+)?(\d{4})\s+Price Change\s+"
        r"(?:Starting\s+" + D + r"\s+through\s+" + D + r"|Effective\s+" + D + r")"
    )

    today_d = datetime.strptime(today or datetime.now().strftime("%Y-%m-%d"),
                                "%Y-%m-%d").date()
    candidates = []
    for m in pat.finditer(text):
        limit, year, start, end, effective = m.groups()
        raw = start or effective
        if not raw:
            continue
        try:
            eff = datetime.strptime(raw, "%B %d, %Y").date()
        except ValueError:
            continue
        if eff > today_d:
            continue  # 尚未生效的公告不能当当前价
        valid_through = None
        if end:
            try:
                valid_through = datetime.strptime(end, "%B %d, %Y").isoformat()
            except ValueError:
                valid_through = None
        candidates.append({
            "effective_date": eff.isoformat(),
            "valid_through": valid_through,
            "name": f"{limit.strip() + ' ' if limit else ''}{year} Price Change",
        })

    if not candidates:
        raise RuntimeError(
            f"Postal Explorer 上没有解析出任何已生效的价格变更 ({PRICE_CHANGE_URL})；"
            "不猜日期，请人工确认后再抓"
        )
    candidates.sort(key=lambda c: c["effective_date"])
    return candidates[-1]


def main() -> int:
    cfg = siteilang.load(os.path.join(ROOT, ".ilang", "site.ilang"))
    files = siteilang.usps_files(cfg)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 先拿生效日。拿不到就不往下走 —— 没有生效日的价格不能发出去。
    try:
        change = fetch_price_change()
    except RuntimeError as exc:
        log(f"FATAL {exc}")
        return 2
    log(f"  price change: {change['name']}  effective={change['effective_date']}"
        f"  through={change['valid_through'] or '—'}")

    out = {
        "meta": {
            "carrier": "USPS",
            "price_index_url": PRICES_BASE,
            "price_change_url": PRICE_CHANGE_URL,
            "price_change": change["name"],
            "effective_date": change["effective_date"],
            "valid_through": change["valid_through"],
            "fetched_at": stamp,
            "stale": False,
            "errors": [],
        },
        "services": {},
        "extra_services": {},
    }

    def stamp_dates(parsed: dict, sid: str, url: str) -> dict:
        """把「文件日期」和「真实生效日」分开记，并交叉校验。

        文件日期应当早于生效日（先发文件、后生效）。如果反过来，
        说明抓到的是一份日期异常的稿子，宁可报错也不发布。
        """
        file_date = parsed.get("file_date")
        eff = change["effective_date"]
        if file_date and file_date > eff:
            raise RuntimeError(
                f"{sid}: 文件日期 {file_date} 晚于生效日 {eff}，数据不一致 ({url})"
            )
        return {
            "effective_date": eff,
            "valid_through": change["valid_through"],
            "price_change": change["name"],
            "file_date": file_date,
        }

    ok = 0
    raw_texts: dict[str, str] = {}
    for spec in files:
        url = PRICES_BASE + spec["filename"]
        try:
            text = fetch(url)
        except RuntimeError as exc:
            log(f"  FAIL  {spec['id']:<30} {exc}")
            out["meta"]["errors"].append({"id": spec["id"], "error": str(exc)})
            continue

        # 解析成功才留原文。解析失败的稿子不进存档，免得以后把一份
        # 有问题的文件当成历史真相重算。

        if spec["id"] in ("extra-services", "other-services"):
            parsed = parse_extra_services(text)
            n = len(parsed["items"])
            if n == 0:
                log(f"  EMPTY {spec['id']:<30} parsed 0 items — dropped")
                out["meta"]["errors"].append({"id": spec["id"], "error": "parsed zero items"})
                continue
            out["extra_services"][spec["id"]] = {
                "label": spec["label"],
                "source_url": url,
                **stamp_dates(parsed, spec["id"], url),
                "items": parsed["items"],
                "price_count": n,
            }
            raw_texts[spec["filename"]] = text
            ok += 1
            log(f"  ok    {spec['id']:<30} {n:4d} items   file={parsed['file_date']}")
            continue

        parsed = parse_price_csv(text)
        n_prices = (
            len(parsed["flat_rate"])
            + sum(len(r["zones"]) for r in (parsed["matrix"] or {}).get("rows", []))
            + len(parsed["fees"])
            + len(parsed["special"])
        )
        if n_prices <= 1:
            log(f"  EMPTY {spec['id']:<30} parsed {n_prices} price(s) — dropped (needs >1)")
            out["meta"]["errors"].append({"id": spec["id"], "error": f"parsed {n_prices} prices"})
            continue

        out["services"][spec["id"]] = {
            "label": spec["label"],
            "basis": spec["basis"],
            "source_url": url,
            **stamp_dates(parsed, spec["id"], url),
            "flat_rate": parsed["flat_rate"],
            "matrix": parsed["matrix"],
            "fees": parsed["fees"],
            "special": parsed["special"],
            "price_count": n_prices,
        }
        raw_texts[spec["filename"]] = text
        ok += 1
        log(
            f"  ok    {spec['id']:<30} {n_prices:4d} prices  file={parsed['file_date']}  "
            f"flat={len(parsed['flat_rate'])} rows={len((parsed['matrix'] or {}).get('rows', []))} "
            f"fees={len(parsed['fees'])} spec={len(parsed['special'])}"
        )

    if ok == 0:
        log("no service parsed — leaving previous data untouched")
        return 1

    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "usps.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)

    log(f"\nwrote {path} — {ok}/{len(files)} sources, {len(out['meta']['errors'])} dropped")

    # 存档放在写 usps.json 之后：数据文件先落盘，存档失败也不会让这次抓取白跑。
    # 但存档失败要报出来，不能静默 —— 丢一天的存档就是永久少一个数据点。
    try:
        manifest = archive.snapshot(raw_texts, {
            "effective_date": change["effective_date"],
            "valid_through": change["valid_through"],
            "price_change": change["name"],
            "source_base": PRICES_BASE,
            "origin": "live-fetch",
        })
        revs = sum(int(r.get("revision") or 1) - 1 for r in manifest["files"].values())
        log(f"archived data/archive/{change['effective_date']}/ — "
            f"{len(manifest['files'])} files" + (f", {revs} revision(s)" if revs else ""))
    except (OSError, ValueError, RuntimeError) as exc:
        log(f"WARNING 存档失败: {exc}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
