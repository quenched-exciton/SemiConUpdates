#!/usr/bin/env python3
# Weekly literature monitor for organic additives in copper electroplating baths
# (damascene, TSV, microvia superfilling, fine-grain/nanotwinned, alkaline copper).
import argparse, datetime as dt, html, json, os, re, sys, time, urllib.parse, urllib.request

# Optional contact email for CrossRef's polite pool; supplied via env (repo
# secret CROSSREF_MAILTO in CI) so it never appears in the public source.
CONTACT_EMAIL = os.environ.get("CROSSREF_MAILTO", "")
LOOKBACK_DAYS = 60
NEW_WINDOW_DAYS = 7
ROWS_PER_QUERY = 1000
MAX_PAGES_PER_QUERY = 30
CROSSREF_ENDPOINT = "https://api.crossref.org/works"
SCOPE_QUERIES = [
    "copper electroplating damascene microvia leveler suppressor accelerator",
    "copper electrodeposition alkaline nanotwinned fine-grain brightener organic",
]
COPPER_ANCHORS_REGEX = re.compile(r"\b(?:copper|cu)\b", re.IGNORECASE)
PLATING_ANCHORS = [
    "electrodeposit", "electroplat", "plating", "electrolyte additive",
    "superfill", "superconform", "bottom-up", "bottom up",
    "damascene", "microvia", "micro-via", "via filling", "via-filling",
    "through-silicon", "through silicon", "tsv",
    "leveler", "leveller", "suppressor", "brightener", "accelerator",
    "nanotwin", "nano-twin", "fine-grain", "fine grain", "grain refin",
    "electrolytic copper", "copper foil",
]
BLOCK_TERMS = [
    "linear accelerator", "financial accelerator", "growth accelerator",
    "accelerator cavity", "electroreduction of co2", "electroreduction of co 2", "co 2 reduction",
    "innovation accelerator", "accelerator programme", "beam", "collider",
    "tumor suppressor", "tumour suppressor", "cancer",
    "dock leveler", "dock leveller",
    "hydrogen evolution", "oxygen evolution", "oxygen reduction",
    "co2 reduction", "carbon dioxide reduction", "ammonia synthesis",
    "nitrate reduction", "water splitting", "photocataly", "electrocataly",
    "fuel cell", " oer", " orr", "urea oxidation", "glycerol",
    "supercapacitor", "battery", "anode for", "cathode for", "lithium", "zinc-air",
    "wastewater", "waste water", "leaching", "biosorption", "adsorption of",
    "solvent extraction", " ore ", " ores", "detoxic", "draw solute",
    "removal of", "remediation",
    "additive manufacturing", "wire arc", "powder bed", "laser melting",
    "selective laser", "cold spray", "directed energy", "3d print", "3d-print",
    "wheat", "rice", "soil", "maize", "crop", "grain quality", "grain safety",
    "aromatic rice", "grain yield",
]
FACET_MAP = [
    ("Leveler", ["leveler", "leveller"]),
    ("Suppressor", ["suppressor", "polyethylene glycol", "peg ", " peg", "peg-", "polyether"]),
    ("Accelerator", ["accelerator", "sps", "mps", "sulfopropyl", "mercapto", "bis(3-sulfopropyl)", "dps"]),
    ("Brightener", ["brightener"]),
    ("Microvia/Superfill", ["microvia", "micro-via", "via filling", "via-filling", "superfill", "superconform", "bottom-up", "bottom up"]),
    ("TSV", ["through-silicon", "through silicon", "tsv"]),
    ("Damascene", ["damascene"]),
    ("Fine-grain/Nanotwinned", ["nanotwin", "nano-twin", "fine-grain", "fine grain", "grain refin", "(111)", "texture"]),
    ("Alkaline system", ["alkaline", "pyrophosphate", "cyanide-free", "cyanide free"]),
    ("Additive (general)", ["additive", "organic addition"]),
]
CORE_FACETS = {"Leveler", "Suppressor", "Accelerator", "Brightener", "Microvia/Superfill", "Additive (general)"}
ALLOWED_TYPES = {"journal-article", "proceedings-article", "posted-content", "book-chapter"}

def build_query_url(terms, date_from, cursor):
    filters = [f"from-created-date:{date_from.isoformat()}"] + [f"type:{t}" for t in sorted(ALLOWED_TYPES)]
    params = {"query.bibliographic": terms, "filter": ",".join(filters),
              "rows": str(ROWS_PER_QUERY), "select": "DOI,title,container-title,created,type",
              "cursor": cursor}
    if CONTACT_EMAIL:
        params["mailto"] = CONTACT_EMAIL
    return CROSSREF_ENDPOINT + "?" + urllib.parse.urlencode(params)

def fetch_live(date_from):
    # Cursor deep-paging: walk EVERY match in the window so the local filter,
    # not CrossRef's relevance ranking, decides what appears on the dashboard.
    all_items = []
    truncated = False
    for terms in SCOPE_QUERIES:
        cursor = "*"
        for _ in range(MAX_PAGES_PER_QUERY):
            url = build_query_url(terms, date_from, cursor)
            req = urllib.request.Request(url, headers={"User-Agent": "cu-plating-monitor/1.0" + (f" (mailto:{CONTACT_EMAIL})" if CONTACT_EMAIL else "")})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
            msg = data.get("message", {})
            items = msg.get("items", [])
            all_items.extend(items)
            cursor = msg.get("next-cursor")
            if len(items) < ROWS_PER_QUERY or not cursor:
                break
            time.sleep(1)
        else:
            truncated = True
    return all_items, truncated

def load_items_from_text(text):
    start = text.find("{"); end = text.rfind("}")
    if start == -1 or end == -1: return []
    try: data = json.loads(text[start:end + 1])
    except json.JSONDecodeError: return []
    return data.get("message", {}).get("items", [])

def load_items_from_files(paths):
    items = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as fh:
            items.extend(load_items_from_text(fh.read()))
    return items

def _first(value, default=""):
    if isinstance(value, list): return value[0] if value else default
    return value if value else default

def parse_created(item):
    parts = item.get("created", {}).get("date-parts", [[]])
    if parts and parts[0] and len(parts[0]) >= 3:
        y, m, d = parts[0][0], parts[0][1], parts[0][2]
        try: return dt.date(y, m, d)
        except ValueError: return None
    return None

def classify(title):
    low = " " + title.lower() + " "
    for bad in BLOCK_TERMS:
        if bad in low: return (False, [], "")
    has_copper = bool(COPPER_ANCHORS_REGEX.search(title))
    has_plating = any(a in low for a in PLATING_ANCHORS)
    if not (has_copper and has_plating): return (False, [], "")
    facets = [label for label, trig in FACET_MAP if any(t in low for t in trig)]
    tier = "core" if (set(facets) & CORE_FACETS) else "adjacent"
    return (True, facets, tier)

def filter_and_rank(raw_items, today):
    seen, records = set(), []
    new_cutoff = today - dt.timedelta(days=NEW_WINDOW_DAYS)
    for item in raw_items:
        if item.get("type") not in ALLOWED_TYPES: continue
        doi = (item.get("DOI") or "").lower()
        if not doi or doi in seen or re.search(r"\.s0\d+$", doi): continue
        title = _first(item.get("title"), "")
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", title)).strip()
        if not title: continue
        keep, facets, tier = classify(title)
        if not keep: continue
        created = parse_created(item)
        if created is None: continue
        seen.add(doi)
        records.append({"doi": item.get("DOI"), "url": "https://doi.org/" + item.get("DOI"),
                        "title": title, "journal": _first(item.get("container-title"), "").strip(),
                        "type": item.get("type"), "created": created.isoformat(),
                        "facets": facets, "tier": tier, "is_new": created >= new_cutoff})
    records.sort(key=lambda r: r["created"], reverse=True)
    records.sort(key=lambda r: (0 if r["is_new"] else 1, 0 if r["tier"] == "core" else 1))
    return records

def build_summary(records, today, truncated=False):
    new_items = [r for r in records if r["is_new"]]
    lines = [f"# Copper-plating additive digest -- week of {today.isoformat()}", ""]
    if not new_items:
        lines.append("_No new copper-plating additive papers were listed in the last 7 days._ The dashboard still shows the trailing 60 days.")
    else:
        lines.append(f"**{len(new_items)} new paper(s) listed in the last 7 days:**"); lines.append("")
        for r in new_items:
            fs = ", ".join(r["facets"]) if r["facets"] else "-"
            lines.append(f"- **{r['title']}** ({r['journal'] or r['type']}, listed {r['created']}) -- {fs} -- {r['url']}")
    lines.append(""); lines.append(f"_Trailing {LOOKBACK_DAYS}-day total: {len(records)} on-topic papers (full deep-paged CrossRef sweep)._")
    if truncated:
        lines.append(""); lines.append("**WARNING: CrossRef paging hit its safety cap (MAX_PAGES_PER_QUERY); this week's sweep may be incomplete.**")
    return "\n".join(lines)

def build_html(records, today):
    new_count = sum(1 for r in records if r["is_new"])
    journals = sorted({r["journal"] for r in records if r["journal"]})
    preprints = sum(1 for r in records if r["type"] == "posted-content")
    facet_counts = {}
    for r in records:
        for f in r["facets"]: facet_counts[f] = facet_counts.get(f, 0) + 1
    facet_counts = dict(sorted(facet_counts.items(), key=lambda kv: kv[1], reverse=True))
    data_json = json.dumps(records, ensure_ascii=False)
    facet_json = json.dumps(facet_counts, ensure_ascii=False)
    generated = today.isoformat()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Copper Electroplating Additive Monitor</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/gridjs@5.0.2/dist/theme/mermaid.min.css" integrity="sha384-jZvDSsmGB9oGGT/4l9bHXGoAv1OxvG/cFmSo0dZaSqmBgvQTKDBFAMftlXTmMbNW" crossorigin="anonymous">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.js" integrity="sha384-iU8HYtnGQ8Cy4zl7gbNMOhsDTTKX02BTXptVP/vqAWIaTfM7isw76iyZCsjL2eVi" crossorigin="anonymous"></script>
<script src="https://cdn.jsdelivr.net/npm/gridjs@5.0.2/dist/gridjs.umd.js" integrity="sha384-/XXDzxe4FsGiAe50i/u9pY/Vy/uX654MHB1xoc1BJNnH1WXHhqHga9g3q5tF4gj7" crossorigin="anonymous"></script>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #f6f7f9; color: #1a1f26; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
  .wrap {{ max-width: 1080px; margin: 0 auto; padding: 24px 20px 60px; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .sub {{ color: #5b6572; font-size: 13px; margin-bottom: 20px; line-height: 1.5; }}
  .sub code {{ background:#eceef1; padding:1px 5px; border-radius:4px; font-size:12px; }}
  .cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 22px; }}
  .card {{ background: #fff; border: 1px solid #e4e7eb; border-radius: 10px; padding: 14px 16px; }}
  .card .n {{ font-size: 26px; font-weight: 650; }}
  .card .l {{ font-size: 12px; color: #6b7280; margin-top: 2px; }}
  .card.high .n {{ color: #b4531a; }}
  .panel {{ background:#fff; border:1px solid #e4e7eb; border-radius:10px; padding:16px 18px; margin-bottom:22px; }}
  .panel h2 {{ font-size: 14px; margin: 0 0 12px; color:#374151; text-transform: uppercase; letter-spacing:.04em; }}
  .chartbox {{ position: relative; height: 260px; }}
  .badge {{ display:inline-block; font-size:11px; font-weight:600; padding:2px 7px; border-radius:20px; margin:1px 3px 1px 0; }}
  .b-core {{ background:#e7f0ff; color:#1d4ed8; }}
  .b-adj {{ background:#f1f0ec; color:#7a6f57; }}
  .b-new {{ background:#ffe9d6; color:#b4531a; }}
  a {{ color: #1d4ed8; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .gridjs-wrapper {{ box-shadow: none; border:1px solid #e4e7eb; }}
  footer {{ color:#8a93a0; font-size:12px; margin-top:26px; line-height:1.6; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Copper Electroplating Additive Monitor</h1>
  <div class="sub">
    Newly listed literature on organic additives for copper electroplating baths in semiconductor manufacturing
    &mdash; damascene, TSV, microvia superfilling, fine-grain / nanotwinned, and alkaline copper systems.<br>
    Source: CrossRef API (<code>from-created-date</code>, full deep-paged sweep). Trailing {LOOKBACK_DAYS} days.
    Updated <b>{generated}</b> &middot; refreshes automatically Mondays 8:00 AM ET.
  </div>
  <div class="cards">
    <div class="card high"><div class="n">{new_count}</div><div class="l">New this week (7 days)</div></div>
    <div class="card"><div class="n">{len(records)}</div><div class="l">On-topic papers ({LOOKBACK_DAYS} days)</div></div>
    <div class="card"><div class="n">{len(journals)}</div><div class="l">Distinct venues</div></div>
    <div class="card"><div class="n">{preprints}</div><div class="l">Preprints included</div></div>
  </div>
  <div class="panel">
    <h2>Papers by additive / topic facet ({LOOKBACK_DAYS} days)</h2>
    <div class="chartbox"><canvas id="facetChart"></canvas></div>
  </div>
  <div class="panel">
    <h2>Papers &mdash; sortable &amp; searchable</h2>
    <div id="table"></div>
  </div>
  <footer>
    Relevance filter requires both a copper anchor and a plating/deposition anchor, then removes look-alike noise
    (linear/financial accelerators, tumor suppressors, dock levelers, electroplating wastewater, OER/HER catalysis,
    additive <i>manufacturing</i>, agricultural grain). &ldquo;Core&rdquo; = bath-additive chemistry;
    &ldquo;Adjacent&rdquo; = related copper topic. Always confirm relevance against the abstract before citing.
  </footer>
</div>
<script>
  const RECORDS = {data_json};
  const FACETS  = {facet_json};
  new Chart(document.getElementById('facetChart'), {{
    type: 'bar',
    data: {{ labels: Object.keys(FACETS), datasets: [{{ label: 'Papers', data: Object.values(FACETS), backgroundColor: '#3b6fd4', borderRadius: 4 }}] }},
    options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }},
      scales: {{ y: {{ beginAtZero: true, ticks: {{ precision: 0 }} }}, x: {{ ticks: {{ autoSkip: false, maxRotation: 40, minRotation: 20 }} }} }} }}
  }});
  function esc(s) {{ return (s || '').replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c])); }}
  const rows = RECORDS.map(r => {{
    const badges = r.facets.map(f => `<span class="badge ${{r.tier === 'core' ? 'b-core' : 'b-adj'}}">${{esc(f)}}</span>`).join('');
    const newBadge = r.is_new ? '<span class="badge b-new">NEW</span> ' : '';
    const titleCell = `${{newBadge}}<a href="${{r.url}}" target="_blank" rel="noopener">${{esc(r.title)}}</a>`;
    return [r.created, titleCell, esc(r.journal || r.type), (badges || '-'), r.tier];
  }});
  new gridjs.Grid({{
    columns: [
      {{ name: 'Listed', width: '96px' }},
      {{ name: 'Title', formatter: (cell) => gridjs.html(cell) }},
      {{ name: 'Venue', width: '150px' }},
      {{ name: 'Facets', formatter: (cell) => gridjs.html(cell), sort: false }},
      {{ name: 'Tier', width: '84px' }},
    ],
    data: rows, search: true, sort: true, pagination: {{ limit: 10 }},
    style: {{ table: {{ 'font-size': '13px' }}, th: {{ 'background': '#f0f2f5' }} }}
  }}).render(document.getElementById('table'));
</script>
</body>
</html>"""

def main():
    parser = argparse.ArgumentParser(description="Weekly CrossRef monitor for copper electroplating bath additives.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--live", action="store_true")
    src.add_argument("--from-json", nargs="+", metavar="FILE")
    src.add_argument("--seed", action="store_true")
    parser.add_argument("--out", default="dashboard.html")
    parser.add_argument("--digest", default="digest.md")
    args = parser.parse_args()
    today = dt.date.today()
    date_from = today - dt.timedelta(days=LOOKBACK_DAYS)
    truncated = False
    if args.live:
        try: raw, truncated = fetch_live(date_from)
        except Exception as exc:
            print(f"Live fetch failed: {exc}", file=sys.stderr); sys.exit(1)
    elif args.from_json:
        raw = load_items_from_files(args.from_json)
    else:
        raw = []
    records = filter_and_rank(raw, today)
    with open(args.out, "w", encoding="utf-8") as fh: fh.write(build_html(records, today))
    with open(args.digest, "w", encoding="utf-8") as fh: fh.write(build_summary(records, today, truncated))
    new_count = sum(1 for r in records if r["is_new"])
    print(f"Kept {len(records)} on-topic papers ({new_count} new this week).")
    if truncated:
        print("WARNING: page cap hit; sweep may be incomplete.", file=sys.stderr)

if __name__ == "__main__":
    main()
