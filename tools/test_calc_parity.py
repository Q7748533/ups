"""计算器页面 JS 与官方 CSV 的逐格一致性校验。

页面上同一个数字有两条独立路径：
  1. build.py 服务端渲染的费率表（走 build.quote()）
  2. 计算器页里那段 JS（走页面自己的取档逻辑）
两条路径任一走偏，页面就会自相矛盾：表格说 $14.85，计算器说 $14.90。
这种矛盾不会有任何报错，只会让读者不再相信这个站。

实现方式：用 node **真跑**页面里的那段 JS（tools/test_calc_js.js 提供最小
DOM 桩），把渲染出来的表格解析回数字，再与 build.quote() 逐格比对。
不在这里复刻一份取价逻辑——复刻版会随实现一起漂移，等于没测。

覆盖：
  - 服务集合一致（计算器不能漏服务，也不能多出未登记的服务）
  - 每个重量档 × Zone 1-9 的价格逐格相等
  - 每个重量档的「最便宜」判定与 Python 侧一致
  - 超出最高档时全部显示「—」而不是错价
  - 中间重量按「第一个 >= 目标的档」取，不插值
  - 官方 CSV 人工核对过的抽查值
"""

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "usps.json"
CALC = ROOT / "site" / "calculator" / "index.html"
HARNESS = ROOT / "tools" / "test_calc_js.js"

NODE = Path(r"C:\Users\cc\.workbuddy-ai\binaries\node\versions\22.22.2-6\node.exe")

# 计算器里显示的服务名 -> data/usps.json 的服务 id
SERVICE_MAP = {
    "Ground Advantage": "ground-advantage-retail",
    "Priority Mail": "priority-mail-retail",
    "Priority Mail Express": "priority-mail-express-retail",
    "Media Mail": "media-mail",
}

failures = []
checks = 0


def fail(msg):
    failures.append(msg)


def load_build():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("shipbuild", ROOT / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # build.py 有 __main__ 守卫，导入不触发生成
    return mod


def run_page_js(cases):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as fh:
        json.dump(cases, fh)
        cases_path = fh.name
    proc = subprocess.run(
        [str(NODE), str(HARNESS), str(CALC), cases_path],
        capture_output=True, text=True, encoding="utf-8",
    )
    Path(cases_path).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise SystemExit(f"FATAL: node 执行页面 JS 失败\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout)


def parse_money(text):
    t = text.strip()
    if not t.startswith("$"):
        return None
    return float(t[1:])


def main():
    global checks
    data = json.loads(DATA.read_text(encoding="utf-8"))
    services = data["services"]
    build = load_build()

    # 用页面自己的数据决定要测哪些 (重量, Zone)，确保覆盖面跟着数据走
    html = CALC.read_text(encoding="utf-8")
    m = re.search(r"var RATES=(\{.*?\});\n", html, re.S)
    if not m:
        raise SystemExit("FATAL: 计算器页面里找不到 RATES")
    rates = json.loads(m.group(1))

    missing = set(SERVICE_MAP) - set(rates)
    extra = set(rates) - set(SERVICE_MAP)
    if missing:
        fail(f"计算器缺少服务: {sorted(missing)}")
    if extra:
        fail(f"计算器多出未登记服务: {sorted(extra)}")

    weights = sorted({t["w"] for tiers in rates.values() for t in tiers})
    top = max(weights)
    # 超出最高档 + 若干中间重量，都要测
    probe = weights + [top + 1, 2.5, 0.9, 15.0]
    cases = [{"w": w, "z": z} for w in probe for z in range(1, 10)]
    got = run_page_js(cases)

    if set(got["services"]) != set(rates):
        fail(f"页面 JS 暴露的服务集合与数据不符: {sorted(got['services'])}")

    by_case = {(r["w"], r["z"]): r for r in got["results"]}

    for w in probe:
        for z in range(1, 10):
            res = by_case.get((w, z))
            checks += 1
            if res is None:
                fail(f"JS 没有返回 w={w} z={z} 的结果")
                continue
            js_prices = {name: parse_money(txt) for name, _lab, txt in res["rows"]}
            if set(js_prices) != set(SERVICE_MAP):
                fail(f"w={w} z={z}: JS 表格服务集合 {sorted(js_prices)}")
                continue

            # 逐服务比对
            for name, svc_id in SERVICE_MAP.items():
                p_py = build.quote(services[svc_id], w, str(z))
                p_js = js_prices[name]
                checks += 1
                if p_py is None and p_js is None:
                    continue
                if p_py is None or p_js is None:
                    fail(f"{name} w={w} z={z}: py={p_py} js={p_js}（一侧缺失）")
                    continue
                if abs(p_py - p_js) > 1e-9:
                    fail(f"{name} w={w} z={z}: py={p_py} js={p_js}")

            # 最便宜判定
            priced = {k: v for k, v in js_prices.items() if v is not None}
            checks += 1
            if not priced:
                if res["cheapest"] is not None:
                    fail(f"w={w} z={z}: 无任何报价却报了最便宜 {res['cheapest']}")
                continue
            exp_best = min(priced.values())
            if res["cheapest"] is None:
                fail(f"w={w} z={z}: 有报价 {exp_best} 但页面没给最便宜结论")
            else:
                got_best = float(res["cheapest"][1][1:])
                if abs(got_best - exp_best) > 1e-9:
                    fail(f"w={w} z={z}: 最便宜应为 {exp_best}，页面报 {got_best}")
                # 服务名也必须对应同一个价
                if priced.get(res["cheapest"][0]) != exp_best:
                    fail(f"w={w} z={z}: 最便宜服务名 {res['cheapest'][0]} 与价格 {exp_best} 不匹配")

    # 中间重量取整档，不插值
    for w_hi, w_lo in ((2.5, 3.0), (0.9, 1.0)):
        for z in (1, 5, 9):
            checks += 1
            a = {n: parse_money(t) for n, _l, t in by_case[(w_hi, z)]["rows"]}
            b = {n: parse_money(t) for n, _l, t in by_case[(w_lo, z)]["rows"]}
            if a != b:
                fail(f"w={w_hi} 与 w={w_lo} z={z} 应取同一档，实际 {a} vs {b}")

    # 官方 CSV 人工核对过的抽查值
    spot = [
        (0.25, 1, "Ground Advantage", 8.40),
        (1.0, 5, "Ground Advantage", 11.70),
        (21.0, 8, "Ground Advantage", 81.25),
        (1.0, 1, "Priority Mail", 11.50),
        (1.0, 5, "Priority Mail", 14.05),
        (1.0, 1, "Media Mail", 4.39),
        (2.0, 1, "Media Mail", 5.13),
    ]
    for w, z, name, expect in spot:
        checks += 1
        row = next((r for r in by_case[(w, z)]["rows"] if r[0] == name), None)
        if row is None:
            fail(f"抽查 {name} {w}lb z{z}: 页面没有这一行")
            continue
        got_p = parse_money(row[2])
        if got_p is None or abs(got_p - expect) > 1e-9:
            fail(f"抽查 {name} {w}lb z{z}: 期望 {expect}，页面 {got_p}")

    # Media Mail 不分 Zone：同一重量九个 Zone 必须同价
    for w in (1.0, 10.0, 40.0):
        checks += 1
        vals = {parse_money(next(r for r in by_case[(w, z)]["rows"] if r[0] == "Media Mail")[2])
                for z in range(1, 10)}
        if len(vals) != 1:
            fail(f"Media Mail {w} lb 出现跨 Zone 差异: {vals}")

    # 无 JS 时的静态表必须等于「控件默认值」对应的 JS 结果。
    # 否则页面一打开，下拉框显示 Zone 1、表格却是 Zone 5 的价格。
    checks += 1
    dw = re.search(r'id="w"[^>]*value="([\d.]+)"', html)
    dz = re.search(r'<option value="(\d)" selected>', html)
    if not dw or not dz:
        fail("计算器页面找不到控件的默认值（缺少 value= 或 selected）")
    else:
        w0, z0 = float(dw.group(1)), int(dz.group(1))
        ssr = re.search(r'<div id="out">(.*?)<p class="note">', html, re.S)
        if not ssr:
            fail("计算器页面缺少静态回落表")
        else:
            ssr_rows = dict(
                (n, parse_money(p))
                for n, _l, p in re.findall(
                    r"<tr><td>(.*?)</td><td>(.*?)</td><td[^>]*>(.*?)</td></tr>",
                    ssr.group(1))
            )
            js_rows = {n: parse_money(t) for n, _l, t in by_case[(w0, z0)]["rows"]}
            if ssr_rows != js_rows:
                fail(f"静态回落表与控件默认值 (w={w0}, z={z0}) 不一致: "
                     f"静态={ssr_rows} JS={js_rows}")

    print(f"checks: {checks}")
    if failures:
        print(f"FAILURES: {len(failures)}")
        for f in failures[:30]:
            print("  -", f)
        return 1
    print("problems: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
