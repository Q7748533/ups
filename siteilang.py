# ::ILANG
# [TYPE:module][PROJECT:parcelrate]
# ::STATE{@SELF, role:parser, reads:.ilang/site.ilang, feeds:[fetch_usps.py, fetch_fedex.py, build.py]}
# ::RULE{配置唯一真源⇒任何承运商清单 文件清单 站点设置都从这个文件读 代码里不许再写一份}
# ::BOUNDARY{never:解析不出来就静默用默认值顶上 必须抛错|scope:permanent}
"""Minimal parser for .ilang/site.ilang — the single source of truth for this site."""

from __future__ import annotations

import os
import re

DEFAULT_PATH = os.path.join(".ilang", "site.ilang")


class IlangConfigError(RuntimeError):
    pass


def _slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return s.strip("-") or "item"


def _strip_comment(line: str) -> str:
    """剥掉行内 `#` 注释。

    `#` 位于行首或前面是空白才算注释起点——这样 URL 片段（`...page#section`）
    和十六进制色值（`#1b5fd9`）前面没有空白，不会被误伤。

    必须在切分字段**之前**剥：注释里常有逗号，先切会把一句话切成好几个
    假字段，值就被污染成 `https://example.com   # 临时地址` 这种半截东西，
    而且不报错。这个坑真实踩过一次。
    """
    for i, ch in enumerate(line):
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _split_fields(body: str) -> dict:
    """Parse `key: value` pairs separated by commas / newlines."""
    out = {}
    for chunk in re.split(r"[,\n]", body):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        k, v = chunk.split(":", 1)
        out[k.strip().strip('"').strip("'")] = v.strip().strip('"').strip("'")
    return out


def load(path: str = DEFAULT_PATH) -> dict:
    if not os.path.exists(path):
        raise IlangConfigError(f"config not found: {path}")

    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    cfg = {"site": {}, "modules": {}, "rules": [], "path": path}
    current = None

    for raw in lines:
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue

        if line.startswith("::STATE{"):
            body = line[len("::STATE{"):].rstrip("}")
            if "@" in body:
                body = body.split(",", 1)[1] if "," in body else ""
            cfg["site"] = _split_fields(body)
            current = None
            continue

        m = re.match(r"^::MODULE\{([A-Z_]+)(?:\|(.*))?\}\s*$", line)
        if m:
            name = m.group(1)
            cfg["modules"][name] = {"title": (m.group(2) or "").strip(), "lines": []}
            current = name
            continue

        if line.startswith("::RULE{") or line.startswith("::BOUNDARY{"):
            cfg["rules"].append(line.strip())
            current = None
            continue

        if line.startswith("::") or line.startswith("[") or line.strip() == "ILANG":
            current = None
            continue

        if current:
            cfg["modules"][current]["lines"].append(line.strip())

    if "CARRIERS" not in cfg["modules"]:
        raise IlangConfigError("::MODULE{CARRIERS} missing — nothing to fetch")

    return cfg


def _pipe_rows(cfg: dict, module: str) -> list[list[str]]:
    """Split `a | b | c` rows of a module into padded field lists."""
    rows = []
    for line in cfg["modules"].get(module, {}).get("lines", []):
        if "|" not in line:
            continue
        rows.append([p.strip() for p in line.split("|")])
    return rows


def carriers(cfg: dict) -> list[dict]:
    """::MODULE{CARRIERS} → [{'id','name','site','source','format'}]"""
    out = []
    for parts in _pipe_rows(cfg, "CARRIERS"):
        parts += [""] * (5 - len(parts))
        cid, name, site, source, fmt = parts[:5]
        if not cid or not source:
            continue
        out.append(
            {
                "id": _slug(cid),
                "name": name or cid,
                "site": site,
                "source": source,
                "format": fmt or "html",
            }
        )
    if not out:
        raise IlangConfigError("::MODULE{CARRIERS} parsed to zero usable rows")
    return out


def usps_files(cfg: dict) -> list[dict]:
    """::MODULE{USPS_FILES} → [{'id','filename','label','basis'}]"""
    out = []
    for parts in _pipe_rows(cfg, "USPS_FILES"):
        parts += [""] * (4 - len(parts))
        fid, filename, label, basis = parts[:4]
        if not fid or not filename:
            continue
        out.append(
            {
                "id": _slug(fid),
                "filename": filename,
                "label": label or fid,
                "basis": basis or "retail",
            }
        )
    if not out:
        raise IlangConfigError("::MODULE{USPS_FILES} parsed to zero usable rows")
    return out


def sources(cfg: dict) -> dict:
    """::MODULE{SOURCES} → {key: url}"""
    out = {}
    for line in cfg["modules"].get("SOURCES", {}).get("lines", []):
        if "|" in line:
            k, v = line.split("|", 1)
            out[k.strip()] = v.strip()
    return out


def seo(cfg: dict) -> dict:
    """::MODULE{SEO} → {key: value}"""
    out = {}
    for line in cfg["modules"].get("SEO", {}).get("lines", []):
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def fields(cfg: dict) -> list[str]:
    """::MODULE{FIELDS} → ordered field names."""
    names = []
    for line in cfg["modules"].get("FIELDS", {}).get("lines", []):
        names += [t for t in re.split(r"[\s,]+", line) if t]
    return names


def settings(cfg: dict) -> dict:
    """::MODULE{SITE} key: value lines, merged over ::STATE{@SITE}."""
    out = dict(cfg["site"])
    for line in cfg["modules"].get("SITE", {}).get("lines", []):
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    out.setdefault("locale", "en-US")
    out.setdefault("currency", "USD")
    out.setdefault("base_url", "")
    return out


if __name__ == "__main__":
    c = load()
    s = settings(c)
    print("brand :", c["site"].get("brand"))
    print("niche :", c["site"].get("niche"))
    print("base  :", s["base_url"])
    print("fields:", fields(c))
    for x in carriers(c):
        print(f"  carrier - {x['name']:<8} {x['id']:<8} {x['format']:<5} {x['source']}")
    for x in usps_files(c):
        print(f"  usps    - {x['id']:<28} {x['basis']:<10} {x['filename']}")
    for k, v in sources(c).items():
        print(f"  source  - {k}: {v}")
    print("  seo     -", seo(c))
