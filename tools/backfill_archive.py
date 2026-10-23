"""把 Postal Explorer 上的历史价格文件回填进 archive。

背景：`usps.com/business/prices/2026/` 每个服务只有一个文件，调价时原地覆盖，
不保留旧版。2025 及更早的年份目录已经 403，年份形式的 Notice 123 URL 虽然
返回 200，内容却是 `FileNotFound` 的 HTML。所以能回填的只有 Postal Explorer
页面上还列着的那两次变更。

已知局限（写进 manifest，不隐藏）：
- July 2026 只提供 `07082026` 这一版 CSV。USPS 同时发布了 `07102026` 的
  Change Log 和 Notice 123 PDF，说明 7/12 生效前还有一次修订，但修订后的
  CSV 没有公开下载。所以这份回填的是 07/08 的构建，可能与最终生效版有差异。
- 更早的调价（2026 年 1 月及以前）已经无法获取，不回填、不推测。

用法：
    python tools/backfill_archive.py            # 只回填，不覆盖已存在的
    python tools/backfill_archive.py --force    # 允许在已存在时重跑校验
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import archive  # noqa: E402
import fetch_usps  # noqa: E402

UA = fetch_usps.UA

JULY_ZIP = ("https://pe.usps.com/resources/PriceChange/"
            "July%202026%20Price%20Change%20-%2007082026%20-%20Notice%20123.zip")

# zip 内的名字 -> 站点使用的名字。已逐个核对文件首行的服务名。
MAPPING = {
    "USPS Ground Advantage Retail.csv": "s-ground-advantage-retail.csv",
    "USPS Ground Advantage Comm.csv": "s-ground-advantage-com.csv",
    "PM Retail.csv": "s-pm-retail.csv",
    "PM Comm.csv": "s-pm-com.csv",
    "PME Retail.csv": "s-pme-retail.csv",
    "PME Comm.csv": "s-pme-com.csv",
    "Media Mail.csv": "m-media-mail-retail-and-comm.csv",
    "Library Mail.csv": "m-library-mail-retail-comm.csv",
    "FCM - Comm Ltrs.csv": "m-fcm-comm-letters.csv",
    "FCM - Comm Flats.csv": "m-fcm-comm-flats.csv",
    "BPM Parcels.csv": "m-bpm-parcels.csv",
    "Dom Extra Svcs.csv": "dom-extra-services.csv",
    "Dom Other Svcs.csv": "dom-other-services.csv",
}

EXTRA = {"dom-extra-services.csv", "dom-other-services.csv"}


def download(url: str, dest: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as rsp:
        data = rsp.read()
    if not data.startswith(b"PK"):
        raise RuntimeError(f"下载的不是 zip（{len(data)} 字节），可能是 FileNotFound 页面: {url}")
    with open(dest, "wb") as fh:
        fh.write(data)


def validate(name: str, text: str) -> str:
    """用正式解析器过一遍，确认这份文件能被正确理解。

    只复制不解析等于把「看不懂的文件」当历史真相存起来。解析不出东西
    就说明格式与我们预期不符，宁可停下。
    """
    if name in EXTRA:
        parsed = fetch_usps.parse_extra_services(text)
        n = len(parsed["items"])
        if n < 10:
            raise RuntimeError(f"{name}: 只解析出 {n} 项附加费，格式可能不符")
        return f"{n} items"

    parsed = fetch_usps.parse_price_csv(text)
    rows = len((parsed["matrix"] or {}).get("rows", []))
    flat = len(parsed["flat_rate"])
    fees = len(parsed["fees"])
    total = rows + flat + fees + len(parsed["special"])
    if total < 2:
        raise RuntimeError(f"{name}: 只解析出 {total} 个价格，格式可能不符")
    return f"{total} prices (rows={rows} flat={flat} fees={fees})"


def main(argv: list[str]) -> int:
    force = "--force" in argv
    target = os.path.join(ROOT, "data", "archive", "2026-07-12")
    if os.path.isdir(target) and not force:
        print(f"{os.path.relpath(target, ROOT)} 已存在，跳过（要重跑加 --force）")
        return 0

    tmp = tempfile.mkdtemp(prefix="backfill-")
    try:
        zpath = os.path.join(tmp, "july.zip")
        print("下载 July 2026 价格包…")
        download(JULY_ZIP, zpath)
        with zipfile.ZipFile(zpath) as zf:
            names = set(zf.namelist())

        missing = [k for k in MAPPING if k not in names]
        if missing:
            raise RuntimeError(f"zip 内缺少 {len(missing)} 个预期文件: {missing[:3]}")

        raw: dict[str, str] = {}
        print("\n逐文件校验：")
        with zipfile.ZipFile(zpath) as zf:
            for zip_name, our_name in MAPPING.items():
                text = zf.read(zip_name).decode("utf-8-sig", errors="replace")
                summary = validate(our_name, text)
                raw[our_name] = text
                print(f"  ok  {our_name:<36} {summary}")

        manifest = archive.snapshot(raw, {
            "effective_date": "2026-07-12",
            "valid_through": None,   # 官方没给终止日；它被 10/4 那版取代
            "price_change": "July 2026 Price Change",
            "source_base": JULY_ZIP,
            "origin": "backfill:postal-explorer-zip (07082026 build)",
        })

        # 记录我们自己观察到的事实：这一版被 10/4 取代。官方没有「superseded_by」
        # 这种字段，所以单独标出来源，免得以后误以为是官方数据。
        day_dir = os.path.join(target)
        mpath = os.path.join(day_dir, "manifest.json")
        import json
        with open(mpath, encoding="utf-8") as fh:
            m = json.load(fh)
        m["superseded_by"] = "2026-10-04"
        m["superseded_by_note"] = "站点观察，非 USPS 官方字段"
        m["caveat"] = (
            "回填自 Postal Explorer 的 07082026 构建。USPS 另发布了 07102026 的 "
            "Change Log 与 Notice 123 PDF，说明 7/12 生效前还有一次修订，但修订后的 "
            "CSV 未公开，本存档可能与该版最终生效稿存在差异。"
        )
        with open(mpath, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(m, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")

        print(f"\n回填完成: data/archive/2026-07-12/ — {len(raw)} files")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
