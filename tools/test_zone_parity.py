"""对拍 Zone 查找：Python 参考实现 vs 页面里真跑的 JS。

前端那段 JS 是用户实际执行的逻辑，Python 的 lookup_zone 是抓取时自检用的
参考实现。两者必须逐条一致 —— 只要有一条不一致，页面上就会显示错的 Zone，
而错的 Zone 直接对应错的价格。

做法：从构建产物里抠出真正内嵌的 <script>，用 node 跑，把结果与 Python 比。
不在 Python 里复刻 JS 逻辑，那样两边会一起漂移。
"""

from __future__ import annotations

import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
NODE = r"C:/Users/cc/.workbuddy-ai/binaries/node/versions/22.22.2-6/node.exe"
NODE_PATH = r"C:/Users/cc/.workbuddy-ai/binaries/node/workspace/node_modules"

ZONES = ROOT / "data" / "zones.json"
PAGE = ROOT / "site" / "rates" / "ground-advantage" / "index.html"


def load_module(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fz = load_module(ROOT / "tools" / "fetch_zones.py", "fz")


def build_cases(data: dict) -> list[dict]:
    """构造用例：覆盖边界 + 每个起点抽样。

    边界用例是手写的，因为这几处是真实踩过的坑：
    - 09012 APO：基础 Zone 1，但 Priority Mail / GA<16oz 是 Zone 5
    - 96913：主表标 9，例外覆盖为 8
    - 96940：不在例外内，保持 9
    """
    cases: list[dict] = []

    # 1. 手写边界（用真实存在的起点）
    for origin in ("10001", "90001", "60601", "33101", "00501"):
        if origin[:3] not in data["origins"]:
            continue
        for dest in ("10001", "11201", "19103", "60601", "75201", "80202",
                     "90001", "96913", "96940", "09012", "34001", "00601"):
            cases.append({"origin": origin, "dest": dest, "service": None})
        for svc in ("Priority Mail", "Ground Advantage", "Priority Mail Express"):
            for dest in ("09012", "96913", "10001"):
                cases.append({"origin": origin, "dest": dest, "service": svc})

    # 2. 非法输入
    for bad in (("1000", "90001"), ("10001", "900"), ("abcde", "90001"), ("", "")):
        cases.append({"origin": bad[0], "dest": bad[1], "service": None})

    # 3. 每个起点随机抽样，覆盖真实分布
    rng = random.Random(20261023)
    origins = sorted(data["origins"])
    dests = [f"{i:03d}{rng.randint(0, 99):02d}" for i in range(0, 1000, 7)]
    for o in origins:
        for _ in range(4):
            cases.append({
                "origin": o + f"{rng.randint(0, 99):02d}",
                "dest": rng.choice(dests),
                "service": None,
            })
    return cases


def main() -> int:
    if not ZONES.exists():
        print(f"缺少 {ZONES.relative_to(ROOT)}，先跑 tools/fetch_zones.py", file=sys.stderr)
        return 2
    if not PAGE.exists():
        print(f"缺少 {PAGE.relative_to(ROOT)}，先跑 build.py", file=sys.stderr)
        return 2

    data = json.loads(ZONES.read_text(encoding="utf-8"))
    cases = build_cases(data)

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="zone-parity-"))
    try:
        cpath = tmp / "cases.json"
        cpath.write_text(json.dumps(cases), encoding="utf-8")
        env = dict(os.environ)
        env["NODE_PATH"] = NODE_PATH
        proc = subprocess.run(
            [NODE, str(ROOT / "tools" / "test_zone_js.js"), str(PAGE), str(ZONES), str(cpath)],
            capture_output=True, text=True, env=env, timeout=180,
        )
        if proc.returncode != 0:
            print("node 执行失败:", proc.stderr[:800], file=sys.stderr)
            return 2
        js = json.loads(proc.stdout)
    finally:
        for f in tmp.glob("*"):
            f.unlink()
        tmp.rmdir()

    fails: list[str] = []
    checks = 0
    SVC_NAMES = ("Priority Mail", "Priority Mail Express", "Ground Advantage")

    for case, got in zip(cases, js["results"]):
        checks += 1
        expect = fz.lookup_zone(data, case["origin"], case["dest"], case["service"])
        if got.get("err"):
            fails.append(f"{case} JS 抛错: {got['err']}")
            continue
        if got["zone"] != expect:
            fails.append(f"{case} JS={got['zone']} Python={expect}")

        # all() 与 find() 必须自洽。计算器靠 all().byService 给 APO/FPO 目的地
        # 逐个服务取 Zone；它和 find() 一旦不一致，同一个地址在计算器和费率页
        # 会显示两个不同的 Zone，而两边都不会报错。
        a = got.get("all")
        checks += 1
        if a is None:
            # 非法 ZIP 两边都返回 null 是正确的；只有「find 有值而 all 空」才是漂移
            if got["zone"] is not None:
                fails.append(f"{case} all() 返回空但 find()={got['zone']}")
            continue

        # 参照值来自逐服务查询（lookup_zone），不是把 all() 的逻辑再抄一遍。
        py_base = fz.lookup_zone(data, case["origin"], case["dest"], None)
        if a["base"] != py_base:
            fails.append(f"{case} all().base={a['base']} 参考={py_base}")

        want = {}
        for name in SVC_NAMES:
            z = fz.lookup_zone(data, case["origin"], case["dest"], name)
            if z is not None and z != py_base:
                want[name] = z
        checks += 1
        if a["byService"] != want:
            fails.append(f"{case} all().byService={a['byService']} 参考={want}")
        for name, z in (a["byService"] or {}).items():
            checks += 1
            if name not in SVC_NAMES:
                fails.append(f"{case} all() 出现未登记服务名 {name}")
            if z == a["base"]:
                fails.append(f"{case} all().byService[{name}]={z} 与基础值相同，不该单独列出")

    checks += 1
    if not js.get("cacheOk"):
        fails.append(f"load() 未缓存：fetch 被调用 {js.get('fetchCalls')} 次")

    print(f"checks: {checks}")
    if fails:
        print(f"FAIL ({len(fails)}):")
        for f in fails[:12]:
            print("  -", f)
        return 1
    print("problems: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
