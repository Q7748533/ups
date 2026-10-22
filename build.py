# ::ILANG
# [TYPE:builder][PROJECT:parcelrate]
# ::STATE{@SELF, role:builder, reads:[.ilang/site.ilang, data/usps.json], writes:site/}
# ::RULE{每个价格表都显示 effective_date 和 source_url⇒缺一个就不渲染那张表}
# ::RULE{所有价格为 published list rate⇒每页必须有 not-negotiated 声明}
# ::RULE{数据行少于 noindex_below_rows⇒该页 noindex 并移出 sitemap}
# ::BOUNDARY{never:编价格 用平均值填表 把不同年份的费率混在一张表里|scope:permanent}
"""Build the static site from data/usps.json + .ilang/site.ilang."""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from html import unescape

import siteilang

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
SITE = os.path.join(ROOT, "site")

cfg = siteilang.load(os.path.join(ROOT, ".ilang", "site.ilang"))
S = siteilang.settings(cfg)
SEO = siteilang.seo(cfg)
BRAND = S.get("brand", "ParcelRate")
NICHE = S.get("niche", "shipping-rates")
BASE = S.get("base_url", "").rstrip("/")
CONTACT = S.get("contact_email", "")
MIN_ROWS = int(SEO.get("noindex_below_rows", "3") or 3)

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
YEAR = datetime.now(timezone.utc).strftime("%Y")

with open(os.path.join(DATA, "usps.json"), encoding="utf-8") as fh:
    USPS = json.load(fh)

SERVICES = USPS["services"]
EXTRA = USPS.get("extra_services", {})
FETCHED_AT = USPS["meta"]["fetched_at"]
STALE = USPS["meta"].get("stale", False)

PAGES: list[dict] = []   # collected for sitemap / index


# --------------------------------------------------------------------------- utils

def esc(text) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def money(v) -> str:
    if v is None:
        return "—"
    if abs(v - round(v)) < 0.005:
        return f"${v:,.2f}"
    return f"${v:,.2f}"


def pretty_date(iso: str | None) -> str:
    if not iso:
        return "not stated"
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
        return d.strftime("%B %-d, %Y") if os.name != "nt" else d.strftime("%B %d, %Y").replace(" 0", " ")
    except ValueError:
        return iso


def url_of(path: str) -> str:
    return f"{BASE}{path}" if BASE else path


# ------------------------------------------------------------------- data access

def svc(key: str) -> dict | None:
    return SERVICES.get(key)


def norm_rows(service: dict) -> list[dict]:
    """Dedupe weight tiers: 15.999 oz and 1 lb collapse to one row, keeping the lb label."""
    matrix = service.get("matrix") or {}
    rows = matrix.get("rows") or []
    best: dict[float, dict] = {}
    for r in rows:
        w = round(r["weight_lb"], 2)
        prev = best.get(w)
        if prev is None:
            best[w] = r
            continue
        # prefer the pound-labelled tier over the ounce-labelled equivalent
        if "oz" in prev["weight_label"] and "oz" not in r["weight_label"]:
            best[w] = r
    return [best[k] for k in sorted(best)]


def zone_list(service: dict) -> list[str]:
    matrix = service.get("matrix") or {}
    return matrix.get("zone_columns") or []


def quote(service: dict, weight_lb: float, zone: str):
    """USPS tiers are 'not over X' — first tier at or above the target weight wins.

    Services priced without zones (Media Mail, Library Mail) carry a single
    'single' column; that price applies regardless of the zone asked for.
    """
    if not service:
        return None
    for r in norm_rows(service):
        if r["weight_lb"] >= weight_lb - 1e-9:
            if "single" in r["zones"]:
                return r["zones"]["single"]
            return r["zones"].get(str(zone))
    return None


def effective_zones(service: dict) -> list[str]:
    """Zone list for comparison purposes: zone-less services behave like all nine zones."""
    if (service.get("matrix") or {}).get("single_price"):
        return [str(i) for i in range(1, 10)]
    return zone_list(service)


def tier_count(service: dict) -> int:
    return len(norm_rows(service))


# ------------------------------------------------------------------------ layout

CSS = """
:root{
  --ink:#16181d; --muted:#5d6470; --line:#e3e6ec; --bg:#ffffff; --soft:#f6f7f9;
  --accent:#1b5fd9; --accent-soft:#eef3fd; --good:#0f7b45; --warn:#8a5a00;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:0 20px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
header.site{border-bottom:1px solid var(--line);background:#fff;position:sticky;top:0;z-index:10}
header.site .wrap{display:flex;align-items:center;gap:22px;height:60px}
.brand{font-weight:700;font-size:17px;letter-spacing:-.01em;color:var(--ink)}
.brand span{color:var(--accent)}
nav.main{display:flex;gap:18px;font-size:14.5px;margin-left:auto;flex-wrap:nowrap;overflow:hidden}
nav.main a{color:var(--muted);white-space:nowrap}
nav.main a:hover{color:var(--ink)}
nav.main a.on{color:var(--ink);font-weight:600}
main{padding:38px 0 60px}
h1{font-size:31px;line-height:1.2;letter-spacing:-.022em;margin:0 0 12px}
h2{font-size:20px;letter-spacing:-.012em;margin:38px 0 12px;padding-top:6px}
h3{font-size:16.5px;margin:26px 0 8px}
p{margin:0 0 14px}
.lede{font-size:17.5px;color:var(--muted);margin-bottom:24px;max-width:70ch}
.crumb{font-size:13px;color:var(--muted);margin-bottom:14px}
.crumb a{color:var(--muted)}
.stamp{display:inline-flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12.5px;
  color:var(--muted);background:var(--soft);border:1px solid var(--line);
  border-radius:6px;padding:7px 11px;margin:0 0 22px}
.stamp b{color:var(--ink);font-weight:600}
table{width:100%;border-collapse:collapse;font-size:14px;margin:14px 0 6px;
  font-variant-numeric:tabular-nums}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:right}
th:first-child,td:first-child{text-align:left}
thead th{background:var(--soft);font-weight:600;font-size:12.5px;letter-spacing:.02em;
  text-transform:uppercase;color:var(--muted);border-bottom:1px solid var(--line)}
tbody tr:hover{background:#fbfcfd}
td.num{font-family:var(--mono);font-size:13.5px}
.best{color:var(--good);font-weight:700}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);
  border-radius:8px;margin:16px 0}
.scroll table{margin:0}
.scroll thead th{position:sticky;top:0}
.note{font-size:13.5px;color:var(--muted);margin:8px 0 0}
.caveat{border-left:3px solid var(--warn);background:#fdf9ef;padding:12px 16px;
  font-size:14px;margin:20px 0;border-radius:0 6px 6px 0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin:22px 0}
.card{border:1px solid var(--line);border-radius:9px;padding:16px 18px;background:#fff}
.card h3{margin:0 0 6px;font-size:15.5px}
.card p{margin:0;font-size:14px;color:var(--muted)}
.card .big{font-family:var(--mono);font-size:21px;color:var(--ink);font-weight:600;
  display:block;margin:4px 0 6px}
ul.tight{margin:0 0 16px;padding-left:20px}
ul.tight li{margin-bottom:7px}
footer.site{border-top:1px solid var(--line);padding:26px 0 40px;font-size:13px;
  color:var(--muted);background:var(--soft)}
footer.site .wrap{display:flex;gap:16px;flex-wrap:wrap}
footer.site a{color:var(--muted)}
.foot-links{margin-left:auto;display:flex;gap:16px;flex-wrap:wrap}
code{font-family:var(--mono);font-size:.9em;background:var(--soft);padding:1px 5px;
  border-radius:4px;border:1px solid var(--line)}
.cta{border:1px solid var(--line);border-radius:9px;padding:18px 20px;margin:26px 0;
  background:var(--soft)}
.cta h3{margin:0 0 8px}
@media(max-width:700px){
  h1{font-size:25px}.lede{font-size:16px}
  /* 移动端 5 项都保留：隐藏第 5 项会让 /ship/ 整个板块在手机上失去入口 */
  nav.main{gap:9px}
  nav.main a{font-size:13px}
  .wrap{padding:0 16px}
  th,td{padding:7px 8px;font-size:13px}
}
"""

NAV = [
    ("/rates/", "Rates"),
    ("/compare/", "Compare"),
    ("/surcharges/", "Fees"),
    ("/calculator/", "Calculator"),
    ("/ship/", "Items"),
]


def _crumb_jsonld(path: str, body: str) -> dict | None:
    """从页面上已经渲染出来的面包屑反推 BreadcrumbList。

    刻意去读可见的 .crumb 而不是另立一套路径->名称映射：结构化数据和
    页面上写着的东西必须永远一致，两套来源迟早会漂。页面上没有面包屑
    就不输出，不编一条用户看不见的层级。
    """
    m = re.search(r'<div class="crumb">(.*?)</div>', body, re.S)
    if not m:
        return None
    items = []
    for i, part in enumerate(re.split(r"\s+/\s+", m.group(1).strip()), start=1):
        link = re.match(r'<a href="([^"]+)">(.*?)</a>', part.strip())
        if link:
            name, url = link.group(2), url_of(link.group(1))
        else:
            name, url = re.sub(r"<[^>]+>", "", part), url_of(path)
        name = unescape(name).strip()
        if not name:
            continue
        items.append({"@type": "ListItem", "position": len(items) + 1,
                      "name": name, "item": url})
    if len(items) < 2:
        return None
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": items}


def ld_block(*blocks) -> str:
    """把多段 JSON-LD 合成一个 script；只有一段时原样输出。"""
    parts = [b for b in blocks if b]
    if not parts:
        return ""
    if len(parts) == 1:
        data = parts[0]
    else:
        flat = []
        for p in parts:
            q = {k: v for k, v in p.items() if k != "@context"}
            flat.append(q)
        data = {"@context": "https://schema.org", "@graph": flat}
    return ('<script type="application/ld+json">'
            + json.dumps(data, ensure_ascii=False) + "</script>")


def layout(path: str, title: str, desc: str, body: str, jsonld: dict | None = None,
           noindex: bool = False) -> str:
    canonical = url_of(path)
    nav_html = "".join(
        f'<a href="{href}"{" class=\'on\'" if path.startswith(href) else ""}>{label}</a>'
        for href, label in NAV
    )
    robots = '<meta name="robots" content="noindex,follow">' if noindex else ""
    ld = ld_block(jsonld, _crumb_jsonld(path, body))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{esc(canonical)}">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<meta name="theme-color" content="#1b5fd9">
{robots}
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{esc(canonical)}">
<meta name="twitter:card" content="summary">
<style>{CSS}</style>
{ld}
</head>
<body>
<header class="site"><div class="wrap">
  <a class="brand" href="/">Parcel<span>Rate</span></a>
  <nav class="main">{nav_html}</nav>
</div></header>
<main><div class="wrap">
{body}
</div></main>
<footer class="site"><div class="wrap">
  <span>{esc(BRAND)} — {esc(NICHE)}. Data from official USPS price files, effective {esc(pretty_date(max((s.get('effective_date') or '') for s in SERVICES.values()) if SERVICES else None))}.</span>
  <span class="foot-links">
    <a href="/about/">About</a><a href="/privacy/">Privacy</a><a href="/sitemap.xml">Sitemap</a>
  </span>
</div></footer>
</body>
</html>"""


def write_page(path: str, html: str, *, priority: str = "0.6", noindex: bool = False,
               lastmod: str | None = None) -> None:
    rel = path.strip("/")
    out = os.path.join(SITE, rel, "index.html") if rel else os.path.join(SITE, "index.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    if not noindex:
        PAGES.append({"path": path, "priority": priority, "lastmod": lastmod or TODAY})


def stamp_block(service: dict) -> str:
    return (
        f'<div class="stamp">'
        f'<span>Effective <b>{esc(pretty_date(service.get("effective_date")))}</b></span>'
        f'<span>·</span><span>Source: <b>USPS official price file</b></span>'
        f'<span>·</span><span>Published list rate</span>'
        f'</div>'
    )


def rate_matrix_table(service: dict, *, max_rows: int = 0, highlight_zone: str | None = None) -> str:
    rows = norm_rows(service)
    zones = zone_list(service)
    if not rows:
        return '<p class="note">No rate table available.</p>'
    if max_rows:
        rows = rows[:max_rows]
    head = "".join(f"<th>Zone {z}</th>" for z in zones)
    body = []
    for r in rows:
        cells = "".join(
            f'<td class="num">{money(r["zones"].get(z))}</td>' for z in zones
        )
        cls = ' class="num"' if False else ""
        body.append(f'<tr><td>{esc(r["weight_label"])}</td>{cells}</tr>')
    return (
        f'<div class="scroll"><table><thead><tr><th>Weight</th>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def flat_rate_table(service: dict) -> str:
    items = service.get("flat_rate") or []
    if not items:
        return ""
    rows = "".join(
        f'<tr><td>{esc(i["name"])}</td><td class="num">{money(i["price"])}</td></tr>'
        for i in items
    )
    return (
        f'<div class="scroll"><table><thead><tr><th>Flat rate option</th>'
        f'<th>Price (any zone)</th></tr></thead><tbody>{rows}</tbody></table></div>'
        f'<p class="note">Flat rate prices do not vary by weight or zone — '
        f'you pay the same no matter where in the U.S. it goes, up to the box or '
        f'envelope\'s weight limit.</p>'
    )


def fee_table(service: dict) -> str:
    items = (service.get("fees") or []) + (service.get("special") or [])
    if not items:
        return ""
    rows = "".join(
        f'<tr><td>{esc(i["name"])}</td><td class="num">{money(i["price"])}</td></tr>'
        for i in items
    )
    return (
        f'<div class="scroll"><table><thead><tr><th>Additional charge</th>'
        f'<th>Amount</th></tr></thead><tbody>{rows}</tbody></table></div>'
    )


LIST_NOTE = (
    '<p class="note">All figures are USPS <b>published list rates</b>. '
    'Commercial or negotiated prices can be lower. Nothing here is an estimate.</p>'
)


# ------------------------------------------------------------------------ pages

def page_home() -> None:
    ga = svc("ground-advantage-retail")
    pm = svc("priority-mail-retail")
    pme = svc("priority-mail-express-retail")
    mm = svc("media-mail")

    cards = []
    if ga:
        v = quote(ga, 1.0, "5")
        cards.append(("/rates/ground-advantage/", "Ground Advantage",
                      money(v), "1 lb to Zone 5 · 2–5 business days"))
    if pm:
        v = quote(pm, 1.0, "5")
        cards.append(("/rates/priority-mail/", "Priority Mail",
                      money(v), "1 lb to Zone 5 · 1–3 business days"))
    if pme:
        v = quote(pme, 1.0, "5")
        cards.append(("/rates/priority-mail-express/", "Priority Mail Express",
                      money(v), "1 lb to Zone 5 · 1–2 days, guaranteed"))
    if mm:
        v = quote(mm, 1.0, "single")
        cards.append(("/rates/media-mail/", "Media Mail",
                      money(v), "1 lb · books, film, music only"))

    card_html = "".join(
        f'<div class="card"><h3><a href="{p}">{esc(t)}</a></h3>'
        f'<span class="big">{v}</span><p>{esc(d)}</p></div>'
        for p, t, v, d in cards
    )

    eff = max((s.get("effective_date") or "") for s in SERVICES.values()) if SERVICES else None

    body = f"""
<div class="crumb">Home</div>
<h1>USPS shipping rates, straight from the official price file</h1>
<p class="lede">Every number on this site comes from the USPS published price files
and carries its effective date. No averages, no "typically costs", no estimates.</p>
<div class="stamp"><span>Data effective <b>{esc(pretty_date(eff))}</b></span>
<span>·</span><span><b>{len(SERVICES)}</b> USPS services parsed</span>
<span>·</span><span>Refreshed every 12 hours</span></div>

<h2>Start here</h2>
<div class="cards">{card_html}</div>

<h2>The comparisons people actually search for</h2>
<ul class="tight">
<li><a href="/compare/media-mail-vs-ground-advantage/">Media Mail vs Ground Advantage</a>
 — the price gap is larger than most people expect, and the rules decide which one you're allowed to use.</li>
<li><a href="/compare/priority-mail-vs-ground-advantage/">Priority Mail vs Ground Advantage</a>
 — what the speed upgrade actually costs, zone by zone.</li>
<li><a href="/compare/flat-rate-vs-regular/">Flat Rate vs regular pricing</a>
 — Flat Rate wins only above a break-even weight, and we show where it is.</li>
<li><a href="/compare/retail-vs-commercial/">Retail vs Commercial rates</a>
 — the same service, two different price sheets.</li>
</ul>

<h2>What makes this different</h2>
<p>Most shipping-cost pages give you a range and a shrug. This site parses the
official USPS CSV price files directly, so every table below is the real tariff —
including the weight tiers, all nine zones, and the surcharges that get added on top.</p>

<div class="caveat"><b>Read this before you budget.</b> These are published list rates.
If you ship with a commercial account, a platform discount, or a reseller, your actual
price will be lower — sometimes a lot lower. We don't guess what your discount is.</div>

<h2>Browse</h2>
<ul class="tight">
<li><a href="/rates/">All USPS rate tables</a> — Ground Advantage, Priority Mail,
Priority Mail Express, Media Mail, Library Mail, Flat Rate.</li>
<li><a href="/surcharges/">Surcharges and extra services</a> — the fees that turn a
$10 label into a $28 label.</li>
<li><a href="/calculator/">Rate calculator</a> — weight and zone in, price out.</li>
<li><a href="/ship/">Cheapest way to ship a specific item</a> — clothes, electronics,
laptops, food, furniture, overnight.</li>
</ul>
"""
    jsonld = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": BRAND,
        "url": url_of("/"),
        "description": "USPS shipping rates from official price files, with effective dates.",
    }
    write_page(
        "/",
        layout(
            "/",
            f"USPS Shipping Rates {YEAR} — Official Price Tables, Not Estimates | {BRAND}",
            "Real USPS shipping rates parsed from the official price files: Ground Advantage, "
            "Priority Mail, Media Mail and Flat Rate, all nine zones, with effective dates and surcharges.",
            body,
            jsonld,
        ),
        priority="1.0",
        lastmod=eff or TODAY,
    )


RATE_PAGES = [
    ("ground-advantage", "ground-advantage-retail", "Ground Advantage",
     "The default USPS ground service for parcels up to 70 lb. Replaced First-Class Package "
     "and Parcel Select Ground in 2023."),
    ("priority-mail", "priority-mail-retail", "Priority Mail",
     "USPS's mid-tier service: 1–3 business days, includes $100 of insurance, and offers "
     "Flat Rate options that ignore weight and zone."),
    ("priority-mail-express", "priority-mail-express-retail", "Priority Mail Express",
     "The fastest USPS option, with a money-back delivery guarantee. The most expensive "
     "per pound by a wide margin."),
    ("media-mail", "media-mail", "Media Mail",
     "A heavily discounted rate restricted to books, film, music, and certain educational "
     "materials. Cheap, slow, and audited."),
    ("library-mail", "library-mail", "Library Mail",
     "Similar to Media Mail but for libraries, museums, and schools. Slightly cheaper, "
     "stricter eligibility."),
]


def page_rates_index() -> None:
    rows = []
    for slug, key, label, blurb in RATE_PAGES:
        s = svc(key)
        if not s:
            continue
        rows.append(
            f'<tr><td><a href="/rates/{slug}/">{esc(label)}</a><br>'
            f'<span class="note">{esc(blurb[:96])}…</span></td>'
            f'<td class="num">{tier_count(s)}</td>'
            f'<td class="num">{len(s.get("flat_rate") or [])}</td>'
            f'<td>{esc(pretty_date(s.get("effective_date")))}</td></tr>'
        )
    body = f"""
<div class="crumb"><a href="/">Home</a> / Rates</div>
<h1>USPS rate tables</h1>
<p class="lede">Full published price tables for each USPS service, parsed from the
official CSV price files. Weight tiers, all nine zones, and the surcharges.</p>
<div class="scroll"><table><thead><tr><th>Service</th><th>Weight tiers</th>
<th>Flat rate options</th><th>Effective</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<p class="note">Zone is determined by the distance between origin and destination ZIP codes.
Zone 9 covers certain destinations outside the contiguous U.S.</p>
{LIST_NOTE}
<h2>Not covered yet</h2>
<p>First-Class Mail letters and flats, Bound Printed Matter, and international services
are not published here. We only list services we can parse accurately — an incomplete
table is better than a wrong one.</p>
"""
    write_page(
        "/rates/",
        layout("/rates/", f"USPS Rate Tables {YEAR} — Every Service, Every Zone | {BRAND}",
               "Full USPS rate tables for Ground Advantage, Priority Mail, Priority Mail Express, "
               "Media Mail, Library Mail and Flat Rate, parsed from official price files.", body),
        priority="0.9",
    )


def page_rate(slug: str, key: str, label: str, blurb: str) -> None:
    s = svc(key)
    if not s:
        return
    tiers = tier_count(s)
    noindex = tiers < MIN_ROWS and len(s.get("flat_rate") or []) < MIN_ROWS
    matrix = rate_matrix_table(s)
    flat = flat_rate_table(s)
    fees = fee_table(s)

    faq = ""
    if slug == "media-mail":
        faq = """
<h2>What counts as Media Mail</h2>
<p>Books with at least 8 pages, sound recordings, recorded video, printed music, and
certain educational materials. Advertising is not allowed beyond incidental. USPS
opens and inspects Media Mail packages — if the contents don't qualify, the package
is returned or charged the higher rate.</p>
<h2>What does not qualify</h2>
<ul class="tight">
<li>Blank notebooks and journals</li>
<li>Comic books with advertising</li>
<li>Board games, puzzles, and toys</li>
<li>Computer media with data or software</li>
<li>Anything with more than incidental advertising</li>
</ul>
"""
    elif slug == "priority-mail":
        faq = """
<h2>When Flat Rate beats weight-based pricing</h2>
<p>Flat Rate is priced by the box, not the scale. On short zones at low weight, regular
Priority Mail is cheaper. On long zones or heavy items, Flat Rate wins — sometimes by a
wide margin. <a href="/compare/flat-rate-vs-regular/">See the break-even points</a>.</p>
<h2>Included with Priority Mail</h2>
<p>$100 of insurance, tracking, and free packaging. Delivery in 1–3 business days
depending on zone.</p>
"""
    elif slug == "ground-advantage":
        faq = """
<h2>What Ground Advantage replaced</h2>
<p>In July 2023 USPS merged First-Class Package Service and Parcel Select Ground into
Ground Advantage. Packages up to 70 lb, 2–5 business days, $100 of insurance included.</p>
<h2>Where the price jumps</h2>
<p>The largest per-pound jump is at the low end — 4 oz to 12 oz costs more than twice as
much, because USPS prices the first tier by handling cost rather than weight.</p>
"""

    body = f"""
<div class="crumb"><a href="/">Home</a> / <a href="/rates/">Rates</a> / {esc(label)}</div>
<h1>{esc(label)} rates {YEAR}</h1>
<p class="lede">{esc(blurb)}</p>
{stamp_block(s)}
{"<h2>Flat Rate options</h2>" + flat if flat else ""}
<h2>Weight and zone pricing</h2>
<p class="note">{tiers} weight tiers × {len(zone_list(s))} zones. Prices are per package.</p>
{matrix}
{LIST_NOTE}
{"<h2>Surcharges on this service</h2>" + fees if fees else ""}
{faq}
<div class="cta"><h3>Need one number, not a table?</h3>
<p>Enter a weight and zone in the <a href="/calculator/">rate calculator</a> and get the
exact published price for every USPS service at once.</p></div>
"""
    write_page(
        f"/rates/{slug}/",
        layout(f"/rates/{slug}/", f"{label} Rates {YEAR} — Full Official Price Table | {BRAND}",
               f"Complete {label} price table from the USPS official price file: {tiers} weight "
               f"tiers across {len(zone_list(s))} zones, plus flat rate options and surcharges. "
               f"Effective {pretty_date(s.get('effective_date'))}.",
               body, noindex=noindex),
        priority="0.8",
        noindex=noindex,
        lastmod=s.get("effective_date") or TODAY,
    )


def page_flat_rate() -> None:
    pm = svc("priority-mail-retail")
    pme = svc("priority-mail-express-retail")
    if not pm and not pme:
        return
    blocks = []
    for s, label, slug in ((pm, "Priority Mail", "priority-mail"),
                           (pme, "Priority Mail Express", "priority-mail-express")):
        if not s or not s.get("flat_rate"):
            continue
        rows = "".join(
            f'<tr><td>{esc(i["name"])}</td><td class="num">{money(i["price"])}</td></tr>'
            for i in s["flat_rate"]
        )
        blocks.append(
            f'<h2><a href="/rates/{slug}/">{esc(label)} Flat Rate</a></h2>'
            f'<div class="scroll"><table><thead><tr><th>Option</th><th>Price</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></div>'
        )
    eff = pm.get("effective_date") if pm else None
    body = f"""
<div class="crumb"><a href="/">Home</a> / <a href="/rates/">Rates</a> / Flat Rate</div>
<h1>USPS Flat Rate prices {YEAR}</h1>
<p class="lede">Flat Rate means the price does not change with weight or destination —
if it fits in the box or envelope and is under the weight limit, you pay the same
whether it crosses the street or the country.</p>
{stamp_block(pm or pme)}
{"".join(blocks)}
<div class="caveat"><b>Weight limits still apply.</b> Priority Mail Flat Rate boxes cap at
70 lb. If your item is heavy and going to a nearby zone, regular weight-based pricing is
usually cheaper. <a href="/compare/flat-rate-vs-regular/">Compare the break-even points</a>.</div>
{LIST_NOTE}
"""
    write_page(
        "/rates/flat-rate/",
        layout("/rates/flat-rate/", f"USPS Flat Rate Box & Envelope Prices {YEAR} | {BRAND}",
               "Every USPS Priority Mail and Priority Mail Express Flat Rate option with its "
               "official published price — envelopes, small, medium, large and APO/FPO boxes.",
               body),
        priority="0.9",
        lastmod=eff or TODAY,
    )


def page_compare(a_key: str, b_key: str, a_name: str, b_name: str, a_slug: str, b_slug: str,
                 intro: str, verdict: str) -> None:
    a, b = svc(a_key), svc(b_key)
    if not a or not b:
        return
    zones = [z for z in effective_zones(a) if z in effective_zones(b)]
    if not zones:
        return
    weights = [1, 2, 5, 10, 20]
    rows = []
    for w in weights:
        cells = []
        for z in zones:
            pa, pb = quote(a, w, z), quote(b, w, z)
            if pa is None or pb is None:
                cells.append('<td class="num">—</td>')
                continue
            cheaper = "best" if pa < pb else ""
            cells.append(
                f'<td class="num"><span class="{cheaper}">{money(pa)}</span> / {money(pb)}</td>'
            )
        rows.append(f'<tr><td>{w} lb</td>{"".join(cells)}</tr>')
    head = "".join(f"<th>Zone {z}</th>" for z in zones)

    eff = max(a.get("effective_date") or "", b.get("effective_date") or "")
    body = f"""
<div class="crumb"><a href="/">Home</a> / <a href="/compare/">Compare</a> / {esc(a_name)} vs {esc(b_name)}</div>
<h1>{esc(a_name)} vs {esc(b_name)} — which is cheaper</h1>
<p class="lede">{esc(intro)}</p>
{stamp_block(a)}
<h2>Side by side, same package</h2>
<p class="note">Each cell shows <b>{esc(a_name)} / {esc(b_name)}</b>. The green figure is
the cheaper of the two at that weight and zone.</p>
<div class="scroll"><table><thead><tr><th>Weight</th>{head}</tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<h2>What the table says</h2>
<p>{esc(verdict)}</p>
<h2>Full tables</h2>
<ul class="tight">
<li><a href="/rates/{a_slug}/">{esc(a_name)} full rate table</a></li>
<li><a href="/rates/{b_slug}/">{esc(b_name)} full rate table</a></li>
</ul>
{LIST_NOTE}
"""
    write_page(
        f"/compare/{a_slug}-vs-{b_slug}/",
        layout(f"/compare/{a_slug}-vs-{b_slug}/",
               f"{a_name} vs {b_name} — Which Is Cheaper? Real Rates, Zone by Zone | {BRAND}",
               f"{a_name} and {b_name} compared at 1, 2, 5, 10 and 20 lb across every zone, "
               f"using official USPS published rates. Effective {pretty_date(eff)}.",
               body),
        priority="0.9",
        lastmod=eff or TODAY,
    )


def breakeven_weight(service: dict, flat_price: float, zone: str):
    """Smallest weight at which weight-based pricing reaches the flat rate."""
    for r in norm_rows(service):
        p = r["zones"].get(zone)
        if p is not None and p >= flat_price:
            return r["weight_lb"], r["weight_label"]
    return None, None


def page_flat_rate_breakeven() -> None:
    pm = svc("priority-mail-retail")
    if not pm or not pm.get("flat_rate"):
        return
    zones = zone_list(pm)
    head = "".join(f"<th>Zone {z}</th>" for z in zones)
    rows = []
    for f in pm["flat_rate"]:
        cells = []
        for z in zones:
            _, label = breakeven_weight(pm, f["price"], z)
            cells.append(f'<td class="num">{label or "never"}</td>')
        rows.append(
            f'<tr><td>{esc(f["name"])}<br><span class="note">{money(f["price"])}</span></td>'
            f'{"".join(cells)}</tr>'
        )
    body = f"""
<div class="crumb"><a href="/">Home</a> / <a href="/compare/">Compare</a> / Flat Rate vs regular</div>
<h1>Flat Rate vs regular pricing — the break-even weight</h1>
<p class="lede">A Flat Rate box costs the same no matter how far it goes. Regular Priority
Mail charges by weight and zone. This table shows the exact weight at which regular
pricing becomes the more expensive of the two.</p>
{stamp_block(pm)}
<h2>How to read this</h2>
<p>Each cell is the weight at which <b>weight-based Priority Mail first costs as much as
or more than</b> that Flat Rate option. Below that weight, regular pricing is cheaper.
Above it, the Flat Rate box wins.</p>
<div class="scroll"><table><thead><tr><th>Flat Rate option</th>{head}</tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<p class="note">"never" means regular pricing does not reach that flat rate at any
published weight tier up to 70 lb — the flat rate is always the more expensive option.</p>
<h2>What this actually means</h2>
<ul class="tight">
<li><b>Short zones favour regular pricing.</b> On Zone 1–2 you need several pounds before a
Small Flat Rate box pays for itself, because local weight-based rates are already cheap.</li>
<li><b>Long zones favour Flat Rate almost immediately.</b> On Zone 8 the 1 lb regular rate
is already above the Small Flat Rate price, so Flat Rate wins from the first pound.</li>
<li><b>Heavy and far is where Flat Rate shines.</b> That is the combination USPS prices
most aggressively on the weight-based side.</li>
</ul>
<div class="caveat"><b>Size still matters.</b> Flat Rate boxes have fixed dimensions. If your
item doesn't fit, the comparison is moot. And every Flat Rate option caps at 70 lb.</div>
{LIST_NOTE}
"""
    write_page(
        "/compare/flat-rate-vs-regular/",
        layout("/compare/flat-rate-vs-regular/",
               f"Flat Rate vs Regular Priority Mail — Exact Break-Even Weight by Zone | {BRAND}",
               "The exact weight where a USPS Flat Rate box becomes cheaper than weight-based "
               "Priority Mail, calculated for every flat rate option and every zone.",
               body),
        priority="0.9",
        lastmod=pm.get("effective_date") or TODAY,
    )


def page_retail_vs_commercial() -> None:
    pairs = [
        ("ground-advantage-retail", "ground-advantage-com", "Ground Advantage"),
        ("priority-mail-retail", "priority-mail-com", "Priority Mail"),
        ("priority-mail-express-retail", "priority-mail-express-com", "Priority Mail Express"),
    ]
    sections = []
    summary = []
    for rk, ck, label in pairs:
        r, c = svc(rk), svc(ck)
        if not r or not c:
            continue
        zones = [z for z in zone_list(r) if z in zone_list(c)]
        if not zones:
            continue
        head = "".join(f"<th>Zone {z}</th>" for z in zones)
        rows = []
        deltas = []
        for w in (1, 2, 5, 10, 20):
            cells = []
            for z in zones:
                pr, pc = quote(r, w, z), quote(c, w, z)
                if pr is None or pc is None:
                    cells.append('<td class="num">—</td>')
                    continue
                pct = (pr - pc) / pr * 100 if pr else 0
                deltas.append(pct)
                cells.append(
                    f'<td class="num">{money(pr)} / {money(pc)}'
                    f'<br><span class="note">−{pct:.0f}%</span></td>'
                )
            rows.append(f'<tr><td>{w} lb</td>{"".join(cells)}</tr>')
        avg = sum(deltas) / len(deltas) if deltas else 0
        summary.append(
            f'<tr><td><a href="/rates/{rk.replace("-retail", "")}/">{esc(label)}</a></td>'
            f'<td class="num">{avg:.0f}%</td></tr>'
        )
        sections.append(
            f'<h2>{esc(label)}</h2>'
            f'<p class="note">Retail / Commercial, with the commercial discount below.</p>'
            f'<div class="scroll"><table><thead><tr><th>Weight</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
        )
    if not sections:
        return
    eff = svc("priority-mail-retail").get("effective_date")
    body = f"""
<div class="crumb"><a href="/">Home</a> / <a href="/compare/">Compare</a> / Retail vs Commercial</div>
<h1>USPS Retail vs Commercial rates</h1>
<p class="lede">USPS publishes two price sheets for the same services. Retail is what you
pay at the counter. Commercial is what you pay with an account or a platform label.
The gap is not a discount you negotiate — it's printed in the tariff.</p>
{stamp_block({"effective_date": eff})}
<h2>Average commercial discount by service</h2>
<div class="scroll"><table><thead><tr><th>Service</th><th>Average gap</th></tr></thead>
<tbody>{"".join(summary)}</tbody></table></div>
{''.join(sections)}
<div class="caveat"><b>Neither sheet is your price.</b> Both are published list rates.
Resellers and platforms negotiate further discounts on top of the commercial sheet, and
those are not public. What this page shows is the part of the gap that is verifiable.</div>
{LIST_NOTE}
"""
    write_page(
        "/compare/retail-vs-commercial/",
        layout("/compare/retail-vs-commercial/",
               f"USPS Retail vs Commercial Rates — How Big Is the Gap? | {BRAND}",
               "USPS publishes two price sheets for the same service. Here is the exact gap "
               "between retail and commercial rates for Ground Advantage, Priority Mail and Express.",
               body),
        priority="0.8",
        lastmod=eff or TODAY,
    )


def page_compare_index() -> None:
    items = [
        ("media-mail-vs-ground-advantage", "Media Mail vs Ground Advantage",
         "Same package, two very different prices — and an eligibility rule that decides it."),
        ("priority-mail-vs-ground-advantage", "Priority Mail vs Ground Advantage",
         "What the speed upgrade costs, zone by zone."),
        ("flat-rate-vs-regular", "Flat Rate vs regular pricing",
         "Flat Rate only wins above a break-even weight. Here is where it flips."),
        ("retail-vs-commercial", "Retail vs Commercial",
         "The same service on two price sheets — the gap is your discount if you have an account."),
    ]
    rows = "".join(
        f'<tr><td><a href="/compare/{s}/">{esc(t)}</a><br><span class="note">{esc(d)}</span></td></tr>'
        for s, t, d in items
    )
    body = f"""
<div class="crumb"><a href="/">Home</a> / Compare</div>
<h1>USPS service comparisons</h1>
<p class="lede">Every comparison here uses the same package, the same weight tiers and the
same zones on both sides. No "it depends" — the numbers are published rates.</p>
<div class="scroll"><table><thead><tr><th>Comparison</th></tr></thead>
<tbody>{rows}</tbody></table></div>
{LIST_NOTE}
"""
    write_page(
        "/compare/",
        layout("/compare/", f"Compare USPS Services — Real Rates, Not Averages | {BRAND}",
               "Media Mail vs Ground Advantage, Priority vs Ground, Flat Rate vs regular, "
               "and Retail vs Commercial — compared with official published rates.", body),
        priority="0.9",
    )


ITEMS = [
    ("clothes", "clothing", "Clothing", "2", "Clothing is light, compresses well, and almost never needs "
     "a box — which makes it one of the few categories where poly mailers beat boxes on price."),
    ("electronics", "electronics", "Electronics", "3", "Electronics are where cheap shipping gets expensive: "
     "dimensional weight, insurance, and carrier restrictions all push the price up."),
    ("laptop", "a laptop", "Laptops", "5", "A laptop is the classic case where the box costs more to ship "
     "than the contents weigh — dimensional weight decides your price, not the scale."),
    ("food", "food and perishables", "Food & perishables", "5", "Perishables add handling fees on top of postage, and "
     "the ice or gel packs count toward billable weight."),
    ("furniture", "furniture", "Furniture", "30", "Furniture is where USPS usually stops making sense — above "
     "a certain size and weight, the parcel networks price themselves out."),
]


def page_item(slug: str, name: str, crumb: str, typical: str, blurb: str) -> None:
    ga = svc("ground-advantage-retail")
    pm = svc("priority-mail-retail")
    if not ga or not pm:
        return
    rows = []
    for w in (1, 2, 3, 5, 10, 20, 30):
        pga, ppm = quote(ga, w, "5"), quote(pm, w, "5")
        if pga is None and ppm is None:
            continue
        cheaper = "best" if (pga is not None and ppm is not None and pga < ppm) else ""
        rows.append(
            f'<tr><td>{w} lb</td><td class="num {cheaper}">{money(pga)}</td>'
            f'<td class="num">{money(ppm)}</td></tr>'
        )
    body = f"""
<div class="crumb"><a href="/">Home</a> / <a href="/ship/">By item</a> / {esc(crumb)}</div>
<h1>Cheapest way to ship {esc(name)}</h1>
<p class="lede">{esc(blurb)}</p>
{stamp_block(ga)}
<div class="caveat"><b>We don't know your package.</b> There is no such thing as "the average
cost to ship {esc(name)}" — it depends on weight, box size and distance. What follows is the
real published price at each weight, so you can pick your own row instead of trusting an
average that was invented by someone else.</div>
<h2>Price by weight, Zone 5</h2>
<p class="note">Zone 5 is a mid-distance shipment. Use the
<a href="/calculator/">calculator</a> for your actual zone.</p>
<div class="scroll"><table><thead><tr><th>Weight</th><th>Ground Advantage</th>
<th>Priority Mail</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<h2>What actually drives the price</h2>
<ul class="tight">
<li><b>Dimensional weight.</b> USPS bills the greater of actual weight and
L×W×H÷166. A light but bulky box is priced as if it were heavy.</li>
<li><b>Zone.</b> The same package can cost twice as much crossing the country as it does
going one state over.</li>
<li><b>Size surcharges.</b> Over 22 inches adds a nonstandard length fee; over 2 cubic
feet adds a volume fee. <a href="/surcharges/">Full list</a>.</li>
</ul>
<h2>Options worth knowing</h2>
<ul class="tight">
<li><a href="/rates/ground-advantage/">Ground Advantage</a> — cheapest for most parcels,
2–5 business days.</li>
<li><a href="/rates/priority-mail/">Priority Mail</a> — 1–3 days, and
<a href="/rates/flat-rate/">Flat Rate</a> ignores weight entirely if it fits.</li>
<li><a href="/rates/priority-mail-express/">Priority Mail Express</a> — when it has to
arrive tomorrow.</li>
</ul>
{LIST_NOTE}
"""
    write_page(
        f"/ship/{slug}/",
        layout(f"/ship/{slug}/",
               f"Cheapest Way to Ship {name.title()} — Actual USPS Rates by Weight | {BRAND}",
               f"What it really costs to ship {name} with USPS, at every weight tier. "
               f"Published rates, not averages, plus the dimensional-weight trap that doubles the bill.",
               body),
        priority="0.8",
        lastmod=ga.get("effective_date") or TODAY,
    )


def page_ship_index() -> None:
    rows = "".join(
        f'<tr><td><a href="/ship/{s}/">Ship {esc(n)}</a></td></tr>' for s, n, _, _, _ in ITEMS
    )
    body = f"""
<div class="crumb"><a href="/">Home</a> / By item</div>
<h1>Cheapest way to ship a specific item</h1>
<p class="lede">Pick the thing you're shipping. Each page gives the real published price at
every weight tier, plus the size rules that decide whether you get charged for weight you
don't have.</p>
<div class="scroll"><table><thead><tr><th>Item</th></tr></thead><tbody>{rows}</tbody></table></div>
<div class="caveat"><b>No averages here.</b> "The average cost to ship a laptop" is a number
someone made up. Weight, box size and distance decide the price — so every page shows the
full table and lets you find your own row.</div>
"""
    write_page(
        "/ship/",
        layout("/ship/", f"Cheapest Way to Ship Anything — Real USPS Rates | {BRAND}",
               "Clothing, electronics, laptops, food, furniture and overnight — with actual "
               "published USPS rates by weight instead of invented averages.", body),
        priority="0.8",
    )


def page_surcharges() -> None:
    blocks = []
    total = 0
    for key in ("extra-services", "other-services"):
        es = EXTRA.get(key)
        if not es:
            continue
        groups: dict[str, list] = {}
        for it in es["items"]:
            groups.setdefault(it["group"] or "Other", []).append(it)
        for g, items in groups.items():
            rows = "".join(
                f'<tr><td>{esc(i["name"])}</td><td class="num">{money(i["price"])}</td></tr>'
                for i in items
            )
            total += len(items)
            blocks.append(
                f'<h2>{esc(g)}</h2><div class="scroll"><table><thead><tr><th>Option</th>'
                f'<th>Price</th></tr></thead><tbody>{rows}</tbody></table></div>'
            )
    pm = svc("priority-mail-retail")
    fee_block = fee_table(pm) if pm else ""
    eff = EXTRA.get("extra-services", {}).get("effective_date")
    body = f"""
<div class="crumb"><a href="/">Home</a> / Fees</div>
<h1>USPS extra services and surcharges {YEAR}</h1>
<p class="lede">The published add-ons that turn a quoted price into an actual bill:
certified mail, signature confirmation, insurance, COD, and the size and handling fees.</p>
{stamp_block({"effective_date": eff})}
<h2>Size and handling surcharges</h2>
<p class="note">These apply on top of the base rate when your package crosses a size threshold.</p>
{fee_block}
{''.join(blocks)}
<p class="note">{total} published line items in total.</p>
{LIST_NOTE}
"""
    write_page(
        "/surcharges/",
        layout("/surcharges/", f"USPS Surcharges & Extra Services {YEAR} — Full Published List | {BRAND}",
               "Every published USPS extra service and surcharge: certified mail, signature, "
               "insurance, COD, nonstandard length and volume fees, hazmat handling.", body),
        priority="0.8",
        lastmod=eff or TODAY,
    )


def page_calculator() -> None:
    ga = svc("ground-advantage-retail")
    pm = svc("priority-mail-retail")
    pme = svc("priority-mail-express-retail")
    mm = svc("media-mail")

    payload = {}
    for key, label in (("ground-advantage-retail", "Ground Advantage"),
                       ("priority-mail-retail", "Priority Mail"),
                       ("priority-mail-express-retail", "Priority Mail Express")):
        s = svc(key)
        if not s:
            continue
        payload[label] = [
            {"w": r["weight_lb"], "label": r["weight_label"], "z": r["zones"]}
            for r in norm_rows(s)
        ]
    if mm:
        payload["Media Mail"] = [
            {"w": r["weight_lb"], "label": r["weight_label"], "z": r["zones"]}
            for r in norm_rows(mm)
        ]

    js = json.dumps(payload, separators=(",", ":"))

    # 无 JS 时的静态回落：默认 2 lb / Zone 5。
    # 计算器页如果只有空容器，爬虫和关掉 JS 的读者看到的是一张白纸。
    # Zone 5 是横跨半国的典型包裹；用 Zone 1（本地）当默认会让首次访客
    # 低估运费。控件默认值必须与这里一致，否则页面一打开数字就和控件矛盾
    # （test_calc_parity.py 的 default_inputs 检查锁住这一点）。
    SSR_W, SSR_Z = 2.0, "5"
    ssr_rows = []
    for name, key in (("Ground Advantage", "ground-advantage-retail"),
                      ("Priority Mail", "priority-mail-retail"),
                      ("Priority Mail Express", "priority-mail-express-retail"),
                      ("Media Mail", "media-mail")):
        s = svc(key)
        if not s:
            continue
        price = quote(s, SSR_W, SSR_Z)
        if price is None:
            continue
        hit = next((r for r in norm_rows(s) if r["weight_lb"] >= SSR_W - 1e-9), None)
        ssr_rows.append((name, hit["weight_label"] if hit else "—", price))
    ssr_best = min((r[2] for r in ssr_rows), default=None)
    ssr_table = "".join(
        f'<tr><td>{n}</td><td>{lab}</td>'
        f'<td class="num{" best" if p == ssr_best else ""}">{money(p)}</td></tr>'
        for n, lab, p in ssr_rows
    )
    ssr_html = (
        f'<div class="scroll"><table><thead><tr><th>Service</th><th>Priced at</th>'
        f'<th>Price</th></tr></thead><tbody>{ssr_table}</tbody></table></div>'
    )
    if ssr_rows:
        winner = next(r for r in ssr_rows if r[2] == ssr_best)
        ssr_html += (f'<p class="note">Cheapest published rate for this weight and zone: '
                     f'<b>{winner[0]} at {money(ssr_best)}</b>.</p>')

    body = f"""
<div class="crumb"><a href="/">Home</a> / Calculator</div>
<h1>USPS rate calculator</h1>
<p class="lede">Enter a weight and a zone. Get the exact published price for every USPS
service at once — no rounding, no "from $X".</p>
{stamp_block(ga or pm)}
<div class="card" style="max-width:520px">
  <label style="display:block;font-size:13px;color:var(--muted);margin-bottom:5px">Weight (lb)</label>
  <input id="w" type="number" min="0.1" step="0.1" value="{SSR_W:g}" inputmode="decimal"
    style="width:100%;padding:9px 11px;border:1px solid var(--line);border-radius:6px;font-size:15px;margin-bottom:14px">
  <label style="display:block;font-size:13px;color:var(--muted);margin-bottom:5px">Zone</label>
  <select id="z" style="width:100%;padding:9px 11px;border:1px solid var(--line);border-radius:6px;font-size:15px">
    {"".join(f'<option value="{i}"{" selected" if i == int(SSR_Z) else ""}>Zone {i}</option>' for i in range(1, 10))}
  </select>
</div>
<h2>Result</h2>
<div id="out">{ssr_html}</div>
<p class="note">Zone is set by the distance between origin and destination ZIP codes.
Zone 1 is local; Zone 8 crosses the country; Zone 9 covers certain non-contiguous destinations.</p>
{LIST_NOTE}
<script>
var RATES={js};
function fmt(v){{return v==null?"—":"$"+v.toFixed(2);}}
function calc(){{
  var w=parseFloat(document.getElementById("w").value);
  var z=document.getElementById("z").value;
  var out=document.getElementById("out");
  if(!w||w<=0){{out.innerHTML='<p class="note">Enter a weight above 0.</p>';return;}}
  var rows=[],best=null,bestName="";
  for(var name in RATES){{
    var tiers=RATES[name],hit=null;
    for(var i=0;i<tiers.length;i++){{if(tiers[i].w>=w-1e-9){{hit=tiers[i];break;}}}}
    if(!hit){{rows.push([name,null,hit]);continue;}}
    var p=hit.z[z];
    // Media Mail / Library Mail 不分 Zone，数据里只有一个 "single" 键；
    // 少了这一步这些服务在计算器里会全部显示「—」，把最便宜的选项藏起来。
    if(p==null)p=hit.z["single"];
    if(p==null){{rows.push([name,null,hit]);continue;}}
    if(best==null||p<best){{best=p;bestName=name;}}
    rows.push([name,p,hit]);
  }}
  var html='<div class="scroll"><table><thead><tr><th>Service</th><th>Priced at</th><th>Price</th></tr></thead><tbody>';
  rows.forEach(function(r){{
    var cls=(r[1]!=null&&r[1]===best)?"num best":"num";
    html+='<tr><td>'+r[0]+'</td><td>'+(r[2]?r[2].label:"—")+'</td><td class="'+cls+'">'+fmt(r[1])+'</td></tr>';
  }});
  html+='</tbody></table></div>';
  if(best!=null){{html+='<p class="note">Cheapest published rate for this weight and zone: <b>'+bestName+' at '+fmt(best)+'</b>.</p>';}}
  out.innerHTML=html;
}}
document.getElementById("w").addEventListener("input",calc);
document.getElementById("z").addEventListener("change",calc);
calc();
</script>
"""
    write_page(
        "/calculator/",
        layout("/calculator/", f"USPS Rate Calculator — Exact Published Prices | {BRAND}",
               "Enter a weight and zone to see the exact published USPS rate for Ground Advantage, "
               "Priority Mail, Priority Mail Express and Media Mail side by side.", body),
        priority="0.8",
        lastmod=(ga or pm).get("effective_date") or TODAY,
    )


def page_about() -> None:
    body = f"""
<div class="crumb"><a href="/">Home</a> / About</div>
<h1>About {esc(BRAND)}</h1>
<p class="lede">A shipping-rate reference built on published tariffs instead of guesses.</p>
<h2>Where the numbers come from</h2>
<p>Every price on this site is parsed directly from the official USPS price files
published at <code>usps.com/business/prices</code>. Each table shows its effective date
and the exact file it came from. Nothing is estimated, averaged, or carried over from an
older price sheet.</p>
<h2>What we don't do</h2>
<ul class="tight">
<li>We don't quote "average" shipping costs. There is no such number — weight, size and
distance decide the price.</li>
<li>We don't mix price sheets from different years. If a carrier hasn't published a new
tariff, we don't show it next to one that has.</li>
<li>We don't guess your commercial discount. All figures are published list rates.</li>
<li>We don't publish a table we can't parse accurately. An incomplete page is better
than a wrong one.</li>
</ul>
<h2>Update cadence</h2>
<p>Price files are re-fetched every {esc(S.get("refresh_human", "12 hours"))}. Last fetch:
{esc(FETCHED_AT)}. USPS changes prices on published effective dates — we show the date
from the file itself, not the date we downloaded it.</p>
<h2>Coverage</h2>
<p>Currently: USPS domestic services only. FedEx and UPS publish their tariffs as
multi-hundred-page PDFs whose tables don't extract reliably; adding them is planned but
not done. We'd rather ship one carrier correctly than three badly.</p>
<p>Questions or corrections: <a href="mailto:{esc(CONTACT)}">{esc(CONTACT)}</a></p>
"""
    write_page(
        "/about/",
        layout("/about/", f"About {BRAND} — Sources, Method, and What We Refuse to Do",
               "Where our shipping rates come from, how they are updated, and the data rules "
               "we follow: no estimates, no averages, no mixed-year comparisons.", body),
        priority="0.5",
    )


def page_privacy() -> None:
    body = f"""
<div class="crumb"><a href="/">Home</a> / Privacy</div>
<h1>Privacy policy</h1>
<p class="lede">Last updated: {esc(pretty_date("2026-10-23"))}</p>
<h2>What we collect</h2>
<p>This site is a static website. It sets no cookies of its own and has no accounts,
no sign-up, and no forms that submit data anywhere. The calculator runs entirely in your
browser — the weight and zone you enter never leave your device.</p>
<h2>Server logs</h2>
<p>Our hosting provider records standard request logs (IP address, user agent, requested
URL, timestamp) for security and abuse prevention. These are retained per the provider's
standard policy.</p>
<h2>Analytics</h2>
<p>If analytics are enabled in future, this section will be updated before they go live,
and any analytics that require consent will not load until consent is given.</p>
<h2>Advertising</h2>
<p>This site currently displays no advertising. If advertising is added, this policy will
be updated first, including any third-party ad vendors and their data practices.</p>
<h2>Third-party links</h2>
<p>Links to carrier websites and shipping services are provided for reference. Those sites
have their own privacy policies, which we don't control.</p>
<h2>Your rights</h2>
<p>Because we don't collect personal data, there is nothing to request or delete. If that
changes, this section will say how to exercise those rights.</p>
<h2>Contact</h2>
<p><a href="mailto:{esc(CONTACT)}">{esc(CONTACT)}</a></p>
"""
    write_page(
        "/privacy/",
        layout("/privacy/", f"Privacy Policy | {BRAND}",
               "What this site collects (almost nothing), how the calculator stays local, "
               "and how we handle analytics and advertising.", body),
        priority="0.3",
        lastmod="2026-10-23",
    )


def page_404() -> None:
    html = layout(
        "/404.html", f"Page not found | {BRAND}",
        "That page does not exist.",
        """<div class="crumb">404</div>
<h1>Page not found</h1>
<p class="lede">That URL doesn't match anything here.</p>
<ul class="tight">
<li><a href="/rates/">All USPS rate tables</a></li>
<li><a href="/compare/">Service comparisons</a></li>
<li><a href="/surcharges/">Surcharges and extra services</a></li>
<li><a href="/calculator/">Rate calculator</a></li>
</ul>""",
        noindex=True,
    )
    with open(os.path.join(SITE, "404.html"), "w", encoding="utf-8") as fh:
        fh.write(html)


def write_sitemap() -> None:
    rows = []
    for p in sorted(PAGES, key=lambda x: (x["path"] != "/", x["path"])):
        rows.append(
            f'<url><loc>{esc(url_of(p["path"]))}</loc>'
            f'<lastmod>{p["lastmod"]}</lastmod>'
            f'<priority>{p["priority"]}</priority></url>'
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(rows)
        + "\n</urlset>\n"
    )
    with open(os.path.join(SITE, "sitemap.xml"), "w", encoding="utf-8") as fh:
        fh.write(xml)


def write_favicon() -> None:
    """写一个 SVG favicon。

    没有它浏览器每次访问都会去要 /favicon.ico 拿一个 404，控制台永远有一条
    红色错误；Google 搜索结果里也没有站点图标。SVG 是 Google 支持的格式，
    且不用维护多尺寸位图。
    """
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="7" fill="#1b5fd9"/>'
        '<path d="M8 13.2h16v10.6a2.2 2.2 0 0 1-2.2 2.2H10.2A2.2 2.2 0 0 1 8 23.8z" fill="#fff"/>'
        '<path d="M8.6 13.2 11.4 8.8h9.2l2.8 4.4z" fill="#fff" opacity=".82"/>'
        '<rect x="14.6" y="8.8" width="2.8" height="17.2" fill="#1b5fd9"/>'
        "</svg>"
    )
    with open(os.path.join(SITE, "favicon.svg"), "w", encoding="utf-8") as fh:
        fh.write(svg)


def write_robots() -> None:
    body = "User-agent: *\nAllow: /\n"
    if BASE:
        body += f"\nSitemap: {BASE}/sitemap.xml\n"
    with open(os.path.join(SITE, "robots.txt"), "w", encoding="utf-8") as fh:
        fh.write(body)


def write_headers() -> None:
    body = """/*
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  X-Frame-Options: SAMEORIGIN
  Permissions-Policy: geolocation=(), microphone=(), camera=()

/*.html
  Cache-Control: public, max-age=300, stale-while-revalidate=86400

/sitemap.xml
  Cache-Control: public, max-age=3600

/robots.txt
  Cache-Control: public, max-age=3600
"""
    with open(os.path.join(SITE, "_headers"), "w", encoding="utf-8") as fh:
        fh.write(body)


def main() -> int:
    if os.path.isdir(SITE):
        shutil.rmtree(SITE)
    os.makedirs(SITE, exist_ok=True)

    page_home()
    page_rates_index()
    for slug, key, label, blurb in RATE_PAGES:
        page_rate(slug, key, label, blurb)
    page_flat_rate()

    page_compare_index()
    page_compare(
        "media-mail", "ground-advantage-retail", "Media Mail", "Ground Advantage",
        "media-mail", "ground-advantage",
        "Media Mail and Ground Advantage ship the same kind of box at very different prices. "
        "The catch is that Media Mail is only legal for specific contents.",
        "Ground Advantage costs more at every weight and every zone — roughly 1.5× to 2× at "
        "the low end. Media Mail only makes sense when your contents legally qualify, and the "
        "gap narrows as weight climbs because both services price handling rather than weight.",
    )
    page_compare(
        "priority-mail-retail", "ground-advantage-retail", "Priority Mail", "Ground Advantage",
        "priority-mail", "ground-advantage",
        "Both move parcels across the same network. Priority Mail buys you 1–3 business days "
        "instead of 2–5, and the difference in price is what that speed actually costs.",
        "The premium is smallest at 1–2 lb on short zones and grows sharply with weight — "
        "by 20 lb the gap is several times the base rate on long zones. If the package isn't "
        "time-sensitive, Ground Advantage is the cheaper answer at almost every weight.",
    )
    page_compare(
        "priority-mail-retail", "media-mail", "Priority Mail", "Media Mail",
        "priority-mail", "media-mail",
        "The widest price gap among USPS services. Media Mail is the cheapest way to move "
        "qualifying printed material, but it's audited and the contents rule is strict.",
        "Media Mail is dramatically cheaper, but it is not a general-purpose parcel service. "
        "If your box holds anything other than qualifying media, USPS can return it or bill "
        "you the higher rate. See the Media Mail eligibility list before relying on it.",
    )

    page_flat_rate_breakeven()
    page_retail_vs_commercial()

    page_ship_index()
    for slug, name, crumb, typical, blurb in ITEMS:
        page_item(slug, name, crumb, typical, blurb)
    page_surcharges()
    page_calculator()
    page_about()
    page_privacy()
    page_404()
    write_sitemap()
    write_robots()
    write_favicon()
    write_headers()

    pages = len(PAGES)
    print(f"built {pages} indexable pages + 404")
    print(f"  services: {len(SERVICES)}  extra-service groups: {len(EXTRA)}")
    print(f"  sitemap entries: {pages}")
    print(f"  output: {SITE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
