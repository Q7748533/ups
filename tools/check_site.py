# ::ILANG
# [TYPE:checker][PROJECT:parcelrate]
# ::STATE{@SELF, role:preflight, reads:site/, exits:0 ok / 1 fail}
# ::RULE{内链断了就失败⇒上线前必须全绿}
# ::RULE{占位符残留就失败⇒模板变量漏渲染会直接暴露在页面上}
"""Preflight checks for the generated site: links, placeholders, JSON-LD, tag balance."""

from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")

HREF_RE = re.compile(r'href="(/[^"#?]*)"')
PLACEHOLDER_PATTERNS = [
    re.compile(r"\{\{[a-z_]+\}\}", re.I),
    re.compile(r"\$\{[a-z_]+\}", re.I),
    re.compile(r"\$[a-z_]{3,}\b"),
    re.compile(r"\{\{[a-z_]+\}"),
]
LD_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
PAIR_TAGS = ["div", "table", "tbody", "thead", "tr", "ul", "section", "header", "footer", "nav", "main"]


def html_files() -> list[str]:
    out = []
    for base, _, names in os.walk(SITE):
        for n in names:
            if n.endswith(".html"):
                out.append(os.path.join(base, n))
    return sorted(out)


def target_exists(path: str) -> bool:
    rel = path.strip("/")
    if rel.endswith(".xml") or rel.endswith(".txt"):
        return os.path.exists(os.path.join(SITE, rel))
    if not rel:
        return os.path.exists(os.path.join(SITE, "index.html"))
    return os.path.exists(os.path.join(SITE, rel, "index.html")) or os.path.exists(
        os.path.join(SITE, rel)
    )


def main() -> int:
    if not os.path.isdir(SITE):
        print("site/ not built")
        return 1

    files = html_files()
    problems: list[str] = []
    checked_links = 0

    for f in files:
        rel = os.path.relpath(f, SITE).replace("\\", "/")
        with open(f, encoding="utf-8") as fh:
            html = fh.read()

        for href in set(HREF_RE.findall(html)):
            checked_links += 1
            if not target_exists(href):
                problems.append(f"[link] {rel} -> {href}")

        for pat in PLACEHOLDER_PATTERNS:
            for m in pat.findall(html):
                problems.append(f"[placeholder] {rel}: {m}")

        for block in LD_RE.findall(html):
            try:
                json.loads(block)
            except json.JSONDecodeError as exc:
                problems.append(f"[jsonld] {rel}: {exc}")

        for tag in PAIR_TAGS:
            opens = len(re.findall(rf"<{tag}[\s>]", html))
            closes = len(re.findall(rf"</{tag}>", html))
            if opens != closes:
                problems.append(f"[tag] {rel}: <{tag}> {opens} open vs {closes} close")

        ids = re.findall(r'\sid="([^"]+)"', html)
        dupes = {i for i in ids if ids.count(i) > 1}
        for d in dupes:
            problems.append(f"[dup-id] {rel}: {d}")

    print(f"files      : {len(files)}")
    print(f"links      : {checked_links} unique internal hrefs")
    print(f"problems   : {len(problems)}")
    for p in problems[:40]:
        print("  " + p)
    if len(problems) > 40:
        print(f"  ... and {len(problems) - 40} more")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
