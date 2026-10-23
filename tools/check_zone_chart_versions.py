"""对比两版 USPS Zone Chart，确认「按月重新发布」是否真的改动了 Zone 划分。

背景：费率 CSV 标 2026-09-14 生效，Zone Chart 却标 2026-10-01 生效。
如果 10-01 那次真的调整了 ZIP 到 Zone 的映射，那么「9/14 的费率 + 10/1 的
Zone」就是跨期组合，同一对 ZIP 在 9 月和 10 月会算出不同价格，页面上的
"effective 2026-09-14" 对部分 ZIP 对就不成立。

做法：同一批 ZIP3 前缀，分别按两个日期取整张 zone chart，剔除 EffectiveDate
后逐行比对。只要有一行不同，就说明划分真的变了。
"""
import json
import sys
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
BASE = "https://postcalc.usps.com/DomesticZoneChart/GetZoneChart"

# 覆盖全美各区域，包含 APO/FPO(090/340/962)、领地(006/967/969)、
# 人口稀疏州(59x/82x) 和三个最大的邮政中心
PREFIXES = [
    "005", "006", "010", "021", "028", "031", "040", "054", "060", "070",
    "080", "090", "100", "112", "142", "152", "170", "191", "200", "212",
    "232", "253", "272", "282", "300", "310", "322", "331", "340", "350",
    "372", "380", "392", "402", "421", "432", "441", "452", "462", "480",
    "490", "500", "522", "531", "551", "553", "571", "581", "590", "600",
    "606", "622", "630", "641", "660", "672", "680", "700", "708", "717",
    "722", "731", "741", "750", "760", "770", "782", "791", "800", "802",
    "810", "820", "832", "840", "850", "852", "871", "880", "891", "900",
    "902", "921", "933", "940", "950", "960", "962", "967", "968", "969",
    "970", "980", "995", "997",
]

COLS = ("Column0", "Column1", "Column2", "Column3")


def fetch(zip3: str, date: str) -> dict:
    url = f"{BASE}?ZIPCode3Digit={zip3}&ShippingDate={date}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def body(d: dict) -> dict:
    """去掉生效日，只留 zone 映射。"""
    return {c: d.get(c, []) for c in COLS}


def main() -> int:
    date_a, date_b = sys.argv[1], sys.argv[2]
    only = sys.argv[3:] or PREFIXES

    diff_prefixes, same, errors = [], 0, []
    total_rows = 0
    eff_a = eff_b = None

    for i, z in enumerate(only, 1):
        try:
            a = fetch(z, date_a)
            time.sleep(0.15)
            b = fetch(z, date_b)
            time.sleep(0.15)
        except Exception as e:  # noqa: BLE001
            errors.append((z, repr(e)[:70]))
            continue

        eff_a = eff_a or a.get("EffectiveDate")
        eff_b = eff_b or b.get("EffectiveDate")

        if body(a) == body(b):
            same += 1
            total_rows += sum(len(a.get(c, [])) for c in COLS)
        else:
            detail = []
            for c in COLS:
                ra, rb = a.get(c, []), b.get(c, [])
                if ra == rb:
                    continue
                for x, y in zip(ra, rb):
                    if x != y:
                        detail.append((c, x.get("ZipCodes"), x.get("Zone"),
                                       y.get("Zone")))
                if len(ra) != len(rb):
                    detail.append((c, f"行数 {len(ra)}->{len(rb)}", "", ""))
            diff_prefixes.append((z, detail[:6], len(detail)))

        if i % 15 == 0:
            print(f"  ...{i}/{len(only)}", file=sys.stderr)

    print(f"对比日期: {date_a} ({eff_a})  vs  {date_b} ({eff_b})")
    print(f"ZIP3 样本: {len(only)}   一致: {same}   有差异: {len(diff_prefixes)}   "
          f"错误: {len(errors)}")
    print(f"一致样本覆盖的映射行数: {total_rows:,}")
    if diff_prefixes:
        print("\n有差异的前缀:")
        for z, detail, n in diff_prefixes:
            print(f"  {z}: {n} 处")
            for c, zp, za, zb in detail:
                print(f"    {c} {zp}: Zone {za} -> {zb}")
    if errors:
        print("\n请求失败:", errors[:5])
    return 0 if not diff_prefixes and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
