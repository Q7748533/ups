"""用 USPS 官方 GetZone 接口独立核验 zones.json，不复用抓取时的解析代码。

为什么要单独一个脚本
--------------------
`test_zone_parity.py` 比的是 Python 参考实现和页面 JS —— 两边一起错就查不出来。
这个脚本走另一条路：拿官方**单个 ZIP 对**的查询接口当裁判，绕开 Zone Chart
那张大表的解析过程。两条路径都通过，才说明数据本身是对的。

裁判接口的已知缺陷（必须补偿，否则会误判）：
- APO/FPO 它一律回 "Zone 1"，是错的。官方自己说 APO 要用另一张表。
  所以 APO 段的用例不参与判定，只报告不判失败。
- 它对同一对 ZIP 可能回多服务结果（"The Zone is 1, except for Priority Mail
  service where the Zone is 5"），这种要按服务分别比。
"""

from __future__ import annotations

import importlib.util
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
ZONES = ROOT / "data" / "zones.json"
API = ("https://postcalc.usps.com/DomesticZoneChart/GetZone"
       "?Origin={o}&Destination={d}&ShippingDate={date}")
# 不带 ShippingDate 官方直接回 "Mailing date is invalid."，一个 zone 都不给。
SHIPPING_DATE = "2026-10-23"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")


def load_module(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fz = load_module(ROOT / "tools" / "fetch_zones.py", "fz")

# APO/FPO 段：裁判接口自己就是错的，不参与判定
APO_PREFIXES = ("090", "091", "092", "093", "094", "095", "096", "097", "098", "340", "962", "963", "964", "965", "966")


def ask(o: str, d: str, retries: int = 3) -> dict | None:
    url = API.format(o=o, d=d, date=SHIPPING_DATE)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as rsp:
                return json.loads(rsp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
            time.sleep(1.2 + attempt)
    return None


def parse_official(text: str) -> dict:
    """把官方回文解析成 {service_key: zone}，service_key 为空串表示通用值。

    实际回文形态：
      "The Zone is 5."                                        -> {"": 5}
      "The Zone is 1, except for Priority Mail service where
       the Zone is 5, and USPS Ground Advantage (< 16 oz.)
       where the Zone is 5"                                   -> {"": 1, "priority mail": 5, ...}
      "This is not a Local Zone. ..."                         -> 不含数字，视为无结论
    """
    out: dict[str, int] = {}
    if not text:
        return out
    m = re.search(r"The Zone is\s+(\d+)", text)
    if m:
        out[""] = int(m.group(1))
    for sm in re.finditer(
        r"except for\s+([A-Za-z0-9 &<>\.\(\)]{3,60}?)\s+service\s+where\s+the\s+Zone\s+is\s+(\d+)",
        text,
    ):
        out[sm.group(1).strip().lower()] = int(sm.group(2))
    # 第二种句式：and USPS Ground Advantage (< 16 oz.) where the Zone is 5
    for sm in re.finditer(
        r"and\s+([A-Za-z0-9 &<>\.\(\)]{3,60}?)\s+where\s+the\s+Zone\s+is\s+(\d+)",
        text,
    ):
        out[sm.group(1).strip().lower()] = int(sm.group(2))
    return out


def official_lookup(o: str, d: str) -> dict:
    """查一次官方，返回 {"raw":..., "zones": {...}}，服务名已归一到我们的叫法。"""
    payload = ask(o, d) or {}
    text = payload.get("ZoneInformation") or ""
    err = (payload.get("PageError") or payload.get("OriginError")
           or payload.get("DestinationError") or payload.get("ShippingDateError") or "")
    zones = parse_official(text)
    norm: dict[str, int] = {}
    for k, v in zones.items():
        if not k:
            norm[""] = v
        elif "priority mail express" in k:
            norm["priority mail express"] = v
        elif "priority mail" in k:
            norm["priority mail"] = v
        elif "ground advantage" in k:
            norm["ground advantage"] = v
    return {"raw": (text or err)[:300], "zones": norm}


def real_zip(zip3: str, rng: random.Random, tries: int = 12) -> str | None:
    """凑一个官方认可的真实 5 位 ZIP。

    Zone Chart 是按 ZIP3 发的，所以表里任何 5 位都能查到 Zone；但裁判接口
    会校验完整 ZIP，编一个不存在的（如 89341）会回 "Origin ZIP Code is invalid"。
    不解决这点，抽样里一半用例根本验证不了。
    """
    seen = set()
    for _ in range(tries):
        cand = zip3 + f"{rng.randint(0, 99):02d}"
        if cand in seen:
            continue
        seen.add(cand)
        payload = ask(cand, "10001") or {}
        msg = (payload.get("OriginError") or "") + (payload.get("PageError") or "")
        if "invalid" not in msg.lower():
            return cand
        time.sleep(0.15)
    return None


def pick_cases(data: dict, n_general: int) -> list[dict]:
    """抽样：真实分布 + 已知坑点。"""
    rng = random.Random(77)
    origins = sorted(data["origins"])
    cases: list[dict] = []

    # 已知坑点必须每次都查
    edge = [
        ("10001", "96913"), ("10001", "96940"), ("10001", "96939"),
        ("10001", "09012"), ("10001", "10001"), ("10001", "11201"),
        ("90001", "90001"), ("60601", "60601"), ("10001", "99501"),
        ("00501", "10001"), ("33101", "10001"), ("80202", "75201"),
        # APO/FPO：官方这里是「基础 Zone + 逐服务例外」，最容易报错价的一段
        ("10001", "09001"), ("90001", "09012"), ("60601", "09330"),
        ("33101", "09096"), ("10001", "96310"), ("90001", "96530"),
        ("10001", "96910"), ("60601", "96950"),
    ]
    for o, d in edge:
        cases.append({"origin": o, "dest": d, "service": None})

    # 真实分布抽样：随机起点 × 随机终点
    pool = [f"{i:03d}" for i in range(1000)]
    for _ in range(n_general):
        o3 = rng.choice(origins)
        d3 = rng.choice(pool)
        if data["origins"][o3][int(d3)] == "-":
            continue
        cases.append({
            "origin": o3 + f"{rng.randint(0, 99):02d}",
            "dest": d3 + f"{rng.randint(0, 99):02d}",
            "service": None,
        })
    return cases


def main(argv: list[str]) -> int:
    n_general = int(argv[0]) if argv else 24
    if not ZONES.exists():
        print(f"缺少 {ZONES}", file=sys.stderr)
        return 2
    data = json.loads(ZONES.read_text(encoding="utf-8"))
    cases = pick_cases(data, n_general)

    fails: list[str] = []
    skipped: list[str] = []
    ok = 0
    apo_ok = 0
    rng = random.Random(5150)
    for i, c in enumerate(cases, 1):
        o, d = c["origin"], c["dest"]
        off = official_lookup(o, d)

        # 编出来的 5 位 ZIP 官方不认，换一个真实的重查（Zone 按 ZIP3 定，不影响结论）
        if "invalid" in off["raw"].lower():
            o = real_zip(o[:3], rng) or o
            d = real_zip(d[:3], rng) or d
            off = official_lookup(o, d)

        zones = off["zones"]
        is_apo = o[:3] in APO_PREFIXES or d[:3] in APO_PREFIXES

        if not zones:
            skipped.append(f"{o}->{d} 官方无结论: {off['raw'][:80]!r}")
            continue
        base = zones.get("")
        if base is None:
            skipped.append(f"{o}->{d} 官方只有服务例外，无通用值")
            continue

        # 官方对 APO/FPO 给的是「基础 Zone + 逐服务例外」。这一段以前整段跳过，
        # 等于把最容易出错的地方排除在验证之外 —— 计算器正是在这里会把
        # Priority Mail 和 Ground Advantage 按基础 Zone 报错价。
        #
        # 这里验的是 Python 参考实现；页面里 all() 与 find() 的一致性由
        # test_zone_parity.py 保证，而 find() 又由本脚本对上官方。
        # 三段串起来，等于 all() 也经过了官方数据的检验，且逻辑不用抄三遍。
        mine_base = fz.lookup_zone(data, o, d, None)

        if is_apo:
            bad = []
            if mine_base != base:
                bad.append(f"基础 我们={mine_base} 官方={base}")
            for name in ("priority mail", "priority mail express", "ground advantage"):
                got = fz.lookup_zone(data, o, d, name)
                want = zones.get(name)
                if want is None:
                    # 官方没为这个服务另设 Zone，说明它跟基础值同区
                    if got is not None and got != base:
                        bad.append(f"{name} 我们={got} 官方未另设（应为 {base}）")
                elif got != want:
                    bad.append(f"{name} 我们={got} 官方={want}")
            if bad:
                fails.append(f"{o}->{d} APO 不一致: " + "; ".join(bad))
            else:
                ok += 1
                apo_ok += 1
            if i % 8 == 0:
                print(f"  …{i}/{len(cases)}", flush=True)
            time.sleep(0.4)
            continue

        if mine_base != base:
            fails.append(f"{o}->{d} 我们={mine_base} 官方={base}  raw={off['raw'][:120]!r}")
        else:
            ok += 1

        if i % 8 == 0:
            print(f"  …{i}/{len(cases)}", flush=True)
        time.sleep(0.4)

    print(f"\n与官方一致: {ok}（其中 APO/FPO 逐服务核对 {apo_ok}）")
    print(f"跳过（官方无结论）: {len(skipped)}")
    for s in skipped[:6]:
        print("  ~", s)
    if fails:
        print(f"FAIL ({len(fails)}):")
        for f in fails[:12]:
            print("  -", f)
        return 1
    print("problems: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
