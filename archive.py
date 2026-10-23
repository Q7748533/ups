# ::ILANG
# [TYPE:module][PROJECT:parcelrate]
# ::STATE{@SELF, role:archivist, reads:[raw USPS price files], writes:[data/archive/]}
# ::RULE{按 effective_date 分目录⇒一次调价一个目录 不按抓取日}
# ::RULE{存原始文件不只存解析结果⇒解析器出错时历史仍可重算}
# ::RULE{md5 相同不重写⇒只更新 last_seen}
# ::RULE{同版内容变了⇒存成 .revN 保留旧版 不覆盖}
# ::BOUNDARY{never:改写已存档的文件 编造历史价格|scope:permanent}
"""Archive USPS price files by effective date.

Why this exists
---------------
`usps.com/business/prices/2026/` serves one file per service and **overwrites it
in place** when USPS changes prices. There is no version history on their side:
the 2025 directory already returns 403, and the yearly Notice 123 URLs for
2025/2024/2023 return HTTP 200 with a `FileNotFound` HTML page inside. Once a
sheet is replaced, the old numbers are gone.

That matters here because the price history is the one thing competitors don't
have. Everyone can paste this month's rate table; almost nobody can say what it
was last July.

What gets stored
----------------
The **raw text of each file**, not the parsed JSON. The parser has had real bugs
(a regex once rejected `15.999`, silently truncating every pound tier). If only
parsed output were archived, a parser bug would corrupt the history too, and it
would do so silently. Keeping the source text means any version can be re-parsed
later with a fixed parser.

Layout
------
    data/archive/
      2026-10-04/
        manifest.json
        raw/s-ground-advantage-retail.csv
        raw/s-pm-retail.csv
        ...
      2026-07-12/
        ...

One directory per effective date. Not per fetch day: the files are fetched every
12 hours and would otherwise produce 700 identical snapshots a year.

Revisions
---------
USPS reissues files inside a single price change (the July 2026 change shipped
an 07/08 build and then an 07/10 build). When the md5 of a file changes under the
same effective date, the new text is stored as `<name>.rev2.csv` and the old one
is left untouched. `manifest.json` records which revision is current, so nothing
is silently overwritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_ROOT = os.path.join(BASE_DIR, "data", "archive")
MANIFEST_NAME = "manifest.json"
RAW_DIR = "raw"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _atomic_write(path: str, content: str) -> None:
    """Write via a temp file so an interrupted run cannot leave half a CSV.

    A truncated price file is worse than a missing one: it parses, it produces
    fewer rows, and nothing complains.
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _read_manifest(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        # A corrupt manifest must not be treated as "no archive yet" and
        # overwritten — that would erase the record of what is on disk.
        raise RuntimeError(f"manifest 损坏，拒绝覆盖: {path}")


def _rev_name(filename: str, revision: int) -> str:
    if revision <= 1:
        return filename
    stem, ext = os.path.splitext(filename)
    return f"{stem}.rev{revision}{ext}"


def snapshot(raw_files: dict[str, str], meta: dict, base_dir: str = BASE_DIR) -> dict:
    """Store one price sheet set under its effective date.

    raw_files : {filename: file text exactly as parsed}
    meta      : {effective_date, valid_through, price_change, source_base, origin}

    Returns the manifest that was written.
    """
    effective = (meta or {}).get("effective_date")
    if not effective:
        raise ValueError("snapshot() 需要 effective_date —— 没有生效日的价格不能存档")
    # 生效日必须是 YYYY-MM-DD，否则会建出乱七八糟的目录名
    datetime.strptime(effective, "%Y-%m-%d")

    day_dir = os.path.join(base_dir, "data", "archive", effective)
    raw_dir = os.path.join(day_dir, RAW_DIR)
    manifest_path = os.path.join(day_dir, MANIFEST_NAME)

    now = _now()
    manifest = _read_manifest(manifest_path)
    if manifest is None:
        manifest = {
            "effective_date": effective,
            "valid_through": meta.get("valid_through"),
            "price_change": meta.get("price_change"),
            "first_seen": now,
            "last_seen": now,
            "encoding": "utf-8, BOM stripped",
            "files": {},
        }
    manifest["last_seen"] = now
    # 生效日/有效期是官方公告给的，每次抓取都可以修正（比如公告改了终止日）
    for key in ("valid_through", "price_change"):
        if meta.get(key):
            manifest[key] = meta[key]

    for name in sorted(raw_files):
        text = raw_files[name]
        digest = _md5(text)
        rec = manifest["files"].get(name)

        if rec is None:
            rel = f"{RAW_DIR}/{name}"
            _atomic_write(os.path.join(day_dir, rel), text)
            manifest["files"][name] = {
                "revision": 1,
                "md5": digest,
                "file": rel,
                "first_seen": now,
                "last_seen": now,
                "source_url": (meta.get("source_base") or "") + name,
                "origin": meta.get("origin") or "live-fetch",
                "bytes": len(text.encode("utf-8")),
            }
            continue

        if rec.get("md5") == digest:
            rec["last_seen"] = now
            continue

        # 同一生效日内官方重发了这份文件 —— 保留旧版，另存新版本
        revision = int(rec.get("revision") or 1) + 1
        rel = f"{RAW_DIR}/{_rev_name(name, revision)}"
        _atomic_write(os.path.join(day_dir, rel), text)
        rec.setdefault("revisions", []).append({
            "revision": rec.get("revision"),
            "md5": rec.get("md5"),
            "file": rec.get("file"),
            "last_seen": rec.get("last_seen"),
        })
        rec.update({
            "revision": revision,
            "md5": digest,
            "file": rel,
            "last_seen": now,
            "bytes": len(text.encode("utf-8")),
        })

    _atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    return manifest


def list_versions(base_dir: str = BASE_DIR) -> list[dict]:
    """Every archived effective date, oldest first, with a compact summary."""
    root = os.path.join(base_dir, "data", "archive")
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        mpath = os.path.join(root, name, MANIFEST_NAME)
        if not os.path.isfile(mpath):
            continue
        try:
            m = _read_manifest(mpath)
        except RuntimeError:
            continue
        if not m:
            continue
        out.append({
            "effective_date": m.get("effective_date", name),
            "valid_through": m.get("valid_through"),
            "price_change": m.get("price_change"),
            "files": len(m.get("files") or {}),
            "revisions": sum(int(r.get("revision") or 1) - 1 for r in (m.get("files") or {}).values()),
            "first_seen": m.get("first_seen"),
            "last_seen": m.get("last_seen"),
        })
    out.sort(key=lambda r: r["effective_date"])
    return out


def main(argv: list[str] | None = None) -> int:
    """Report what is on record. Archiving itself happens during fetch."""
    versions = list_versions()
    if not versions:
        print("archive: empty — run fetch_usps.py to record the current sheet")
        return 0
    print(f"archive: {len(versions)} price sheet(s)")
    for v in versions:
        extra = f", {v['revisions']} revision(s)" if v["revisions"] else ""
        through = f" through {v['valid_through'][:10]}" if v.get("valid_through") else ""
        print(f"  {v['effective_date']}{through}  {v['files']} files{extra}  "
              f"[{v['price_change'] or '—'}]")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
