"""archive.py 的回归测试。

重点不是「能不能存」，而是几条会静默毁掉历史的行为：
- 同一生效日内容变了，旧版必须留着（USPS 真的会在一版里重发文件）
- 重复抓取不能把 last_seen 覆盖成第一次的时间
- manifest 损坏时必须报错，不能当成「还没存档」然后覆盖掉
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_archive():
    spec = importlib.util.spec_from_file_location("shiparchive", ROOT / "archive.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


A = load_archive()
fails: list[str] = []
checks = 0


def check(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    if not cond:
        fails.append(msg)


META = {
    "effective_date": "2026-10-04",
    "valid_through": "2027-01-17T00:00:00",
    "price_change": "Time-Limited 2026 Price Change",
    "source_base": "https://example.test/",
    "origin": "test",
}

CSV_A = "Service,Final,,9/14/2026\nWeight,Zone 1\n1,8.40\n"
CSV_B = "Service,Final,,9/14/2026\nWeight,Zone 1\n1,9.99\n"


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="archive-test-")
    try:
        # 1. 首次存档
        m1 = A.snapshot({"svc.csv": CSV_A, "other.csv": CSV_A}, META, base_dir=tmp)
        day = os.path.join(tmp, "data", "archive", "2026-10-04")
        raw = os.path.join(day, "raw")
        check(len(m1["files"]) == 2, "首次存档应记录 2 个文件")
        check(os.path.isfile(os.path.join(raw, "svc.csv")), "原始文件应落盘")
        check(m1["files"]["svc.csv"]["revision"] == 1, "首次应为 revision 1")
        check(m1["files"]["svc.csv"]["md5"] == A._md5(CSV_A), "md5 应为内容摘要")
        with open(os.path.join(raw, "svc.csv"), encoding="utf-8", newline="") as fh:
            check(fh.read() == CSV_A, "存档内容必须与原文逐字节一致")

        # 2. 同内容重复存档：不新增文件、不产生 revision
        before_files = sorted(os.listdir(raw))
        m2 = A.snapshot({"svc.csv": CSV_A, "other.csv": CSV_A}, META, base_dir=tmp)
        check(sorted(os.listdir(raw)) == before_files, "内容未变时不应新增文件")
        check(m2["files"]["svc.csv"]["revision"] == 1, "内容未变时 revision 不应增加")
        check(m2["files"]["svc.csv"]["first_seen"] == m1["files"]["svc.csv"]["first_seen"],
              "first_seen 不应被重复抓取改写")

        # 3. 同一生效日内容变了：保留旧版，另存 rev2
        m3 = A.snapshot({"svc.csv": CSV_B, "other.csv": CSV_A}, META, base_dir=tmp)
        rec = m3["files"]["svc.csv"]
        check(rec["revision"] == 2, "内容变化应升到 revision 2")
        check(rec["file"].endswith("svc.rev2.csv"), f"新版本应存为 .rev2.csv，实际 {rec['file']}")
        check(os.path.isfile(os.path.join(raw, "svc.csv")), "旧版文件必须保留")
        with open(os.path.join(raw, "svc.csv"), encoding="utf-8", newline="") as fh:
            check(fh.read() == CSV_A, "旧版内容不能被覆盖")
        with open(os.path.join(raw, "svc.rev2.csv"), encoding="utf-8", newline="") as fh:
            check(fh.read() == CSV_B, "新版内容应正确写入")
        check(len(rec.get("revisions") or []) == 1, "应记录 1 条历史 revision")
        check(rec["revisions"][0]["md5"] == A._md5(CSV_A), "历史 revision 应保留旧 md5")

        # 4. 再抓一次同样的新内容：不应继续升 revision
        m4 = A.snapshot({"svc.csv": CSV_B, "other.csv": CSV_A}, META, base_dir=tmp)
        check(m4["files"]["svc.csv"]["revision"] == 2, "同样内容重复存档不应继续升版")

        # 5. 缺 effective_date 必须报错
        try:
            A.snapshot({"svc.csv": CSV_A}, {"price_change": "x"}, base_dir=tmp)
            check(False, "缺 effective_date 时应抛 ValueError")
        except ValueError:
            check(True, "")

        # 6. 生效日格式非法必须报错（否则会建出乱七八糟的目录名）
        try:
            A.snapshot({"svc.csv": CSV_A}, {"effective_date": "../../etc"}, base_dir=tmp)
            check(False, "非法 effective_date 应抛错")
        except ValueError:
            check(True, "")

        # 7. manifest 损坏时必须报错，不能当成空存档覆盖
        mpath = os.path.join(day, "manifest.json")
        with open(mpath, "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        try:
            A.snapshot({"svc.csv": CSV_A}, META, base_dir=tmp)
            check(False, "manifest 损坏时应抛 RuntimeError")
        except RuntimeError:
            check(True, "")

        # 8. list_versions 摘要
        with open(mpath, "w", encoding="utf-8") as fh:
            json.dump(m4, fh)
        vs = A.list_versions(base_dir=tmp)
        check(len(vs) == 1, "应有 1 个存档版本")
        check(vs[0]["effective_date"] == "2026-10-04", "版本生效日应为 2026-10-04")
        check(vs[0]["revisions"] == 1, "摘要里应显示 1 次修订")

        # 9. 空目录不报错
        empty = tempfile.mkdtemp(prefix="archive-empty-")
        check(A.list_versions(base_dir=empty) == [], "没有存档时应返回空列表")
        shutil.rmtree(empty, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"checks: {checks}")
    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("problems: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
