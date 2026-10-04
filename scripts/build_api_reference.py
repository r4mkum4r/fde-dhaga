"""
Build the shareable API reference (docs/api/index.html + docs/api/openapi.json).

Everything in it comes from the running code: the schema from FastAPI's OpenAPI output
(app/schemas.py), and every example from a real call against a throwaway copy of the
database. Rerun after any API change:

    .venv/bin/python scripts/build_api_reference.py
"""
import html
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import analytics, main  # noqa: E402
from app.db import Database  # noqa: E402

OUT = ROOT / "docs" / "api"
E = html.escape


def md(text):
    """Escape, then render `backticks` as code."""
    parts = E(text or "").split("`")
    return "".join(f"<code>{p}</code>" if i % 2 else p for i, p in enumerate(parts))

# ---------------------------------------------------------------------------
# Real responses
# ---------------------------------------------------------------------------
tmp = tempfile.mkdtemp()
main.db = Database(url="", sqlite_path=Path(tmp) / "ref.sqlite")
main._cache.update(version=None, value=None)
client = TestClient(main.app)
spec = client.get("/api/openapi.json").json()
SCHEMAS = spec["components"]["schemas"]


def trim(obj, keep=2):
    """Shorten long lists so examples stay readable; say how many were cut."""
    if isinstance(obj, list):
        items = [trim(x, keep) for x in obj[:keep]]
        if len(obj) > keep:
            items.append(f"… {len(obj) - keep} more")
        return items
    if isinstance(obj, dict):
        return {k: trim(v, keep) for k, v in obj.items()}
    return obj


summary = client.get("/api/summary").json()
spot = summary["hotspots"][0]
examples = {
    ("get", "/health"): client.get("/health").json(),
    ("get", "/api/summary"): trim({**summary, "drivers": summary["drivers"]}, 2),
    ("get", "/api/trend"): client.get("/api/trend", params={"issue": "too_small"}).json(),
    ("get", "/api/returns"): trim(client.get("/api/returns", params={"vendor_id": spot["vendor_id"], "subcategory": spot["subcategory"], "issue_type": "too_small"}).json(), 1),
}
post_body = {"return_id": "RT000556", "model_issue_type": "too_small", "is_correct": False, "corrected_issue": "too_large"}
examples[("post", "/api/corrections")] = client.post("/api/corrections", json=post_body).json()
examples[("get", "/api/corrections")] = client.get("/api/corrections").json()
error_examples = {
    "404": client.post("/api/corrections", json={**post_body, "return_id": "RT999999"}).json(),
    "400": client.get("/api/trend", params={"issue": "fabric_quality"}).json(),
}
request_examples = {
    ("get", "/api/trend"): "/api/trend?issue=too_small",
    ("get", "/api/returns"): f"/api/returns?vendor_id={spot['vendor_id']}&subcategory={spot['subcategory']}&issue_type=too_small",
}
sample_comment = next(r for r in client.get("/api/returns", params={"vendor_id": "V07"}).json()["returns"]
                      if r["evidence_phrase"])

# ---------------------------------------------------------------------------
# Schema rendering
# ---------------------------------------------------------------------------
def ref_name(s):
    return s["$ref"].rsplit("/", 1)[-1] if "$ref" in s else None


def type_str(s):
    if "$ref" in s:
        n = ref_name(s)
        return f'<a href="#schema-{n}">{n}</a>'
    if "anyOf" in s:
        parts = [type_str(x) for x in s["anyOf"]]
        return " | ".join(parts)
    if "const" in s:
        return f"<code>{E(json.dumps(s['const']))}</code>"
    if "enum" in s:
        return " | ".join(f"<code>{E(str(v))}</code>" for v in s["enum"])
    t = s.get("type")
    if t == "array":
        return f"array of {type_str(s.get('items', {}))}"
    if t == "object" and "additionalProperties" in s:
        return f"object: key → {type_str(s['additionalProperties'])}"
    return {"string": "string", "integer": "integer", "number": "number", "boolean": "boolean", "null": "null"}.get(t, t or "any")


def schema_table(name):
    s = SCHEMAS[name]
    req = set(s.get("required", []))
    rows = []
    for field, f in s.get("properties", {}).items():
        desc = f.get("description", "")
        ex = f.get("examples")
        extra = []
        if "minItems" in f and f.get("minItems") == f.get("maxItems"):
            extra.append(f"exactly {f['minItems']} items")
        if ex:
            extra.append("e.g. " + ", ".join(f"<code>{E(json.dumps(x, ensure_ascii=False))}</code>" for x in ex[:1]))
        rows.append(f"""<tr><td><code class="f">{E(field)}</code>{'' if field in req else '<span class="opt">optional</span>'}</td>
          <td class="t">{type_str(f)}</td><td>{md(desc)}{(' <span class="ex">' + ' · '.join(extra) + '</span>') if extra else ''}</td></tr>""")
    return f"""<div class="tbl"><table><thead><tr><th>Field</th><th>Type</th><th>Meaning</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>"""


def schemas_used(name, seen=None):
    seen = seen if seen is not None else []
    if name in seen:
        return seen
    seen.append(name)
    def walk(s):
        if isinstance(s, dict):
            if "$ref" in s:
                schemas_used(ref_name(s), seen)
            for v in s.values():
                walk(v)
        elif isinstance(s, list):
            for v in s:
                walk(v)
    walk(SCHEMAS[name].get("properties", {}))
    return seen


def code(obj):
    return f'<pre class="code"><code>{E(json.dumps(obj, indent=2, ensure_ascii=False))}</code></pre>'


# ---------------------------------------------------------------------------
# Endpoint sections
# ---------------------------------------------------------------------------
PANEL = {
    "/health": "Deploy check and keep-alive ping. Not called by the page.",
    "/api/summary": "Headline tiles, What to fix first, At Dhaga's scale, Return drivers, Problem spots, Locations.",
    "/api/trend": "Trend chart (and clicking a bar in Return drivers).",
    "/api/returns": "Comments drawer: a problem spot, a city, or the Unclear / Failed lists.",
    "/api/corrections": "✓ / ✗ buttons in the drawer and the Label accuracy tile.",
}
ORDER = [("get", "/api/summary"), ("get", "/api/trend"), ("get", "/api/returns"),
         ("get", "/api/corrections"), ("post", "/api/corrections"), ("get", "/health")]


def slug(method, path):
    return f"{method}-{path.strip('/').replace('/', '-')}"


def endpoint(method, path):
    op = spec["paths"][path][method]
    params = op.get("parameters", [])
    ptable = ""
    if params:
        rows = "".join(
            f"<tr><td><code class='f'>{E(p['name'])}</code>{'' if p.get('required') else '<span class=opt>optional</span>'}</td>"
            f"<td class='t'>{type_str(p['schema'])}</td><td>{md(p.get('description') or p['schema'].get('description', ''))}"
            + (f" <span class='ex'>e.g. <code>{E(str((p.get('examples') or p['schema'].get('examples') or [''])[0]))}</code></span>" if (p.get('examples') or p['schema'].get('examples')) else "")
            + (f" <span class='ex'>default <code>{E(str(p['schema']['default']))}</code></span>" if 'default' in p['schema'] and p['schema']['default'] is not None else "")
            + "</td></tr>" for p in params)
        ptable = f"<h4>Query parameters</h4><div class='tbl'><table><thead><tr><th>Name</th><th>Type</th><th>Meaning</th></tr></thead><tbody>{rows}</tbody></table></div>"
    body = ""
    if "requestBody" in op:
        bname = ref_name(op["requestBody"]["content"]["application/json"]["schema"])
        body = f"<h4>Request body <a class='sref' href='#schema-{bname}'>{bname}</a></h4>{schema_table(bname)}<h4>Example request</h4>{code(post_body)}"
    resp_rows = []
    for status, r in sorted(op["responses"].items()):
        sch = r.get("content", {}).get("application/json", {}).get("schema", {})
        n = ref_name(sch)
        resp_rows.append(f"<tr><td><span class='st st{status[0]}'>{status}</span></td><td>{md(r.get('description', ''))}</td>"
                         f"<td>{f'<a href=#schema-{n}>{n}</a>' if n else ''}</td></tr>")
    resp = f"<h4>Responses</h4><div class='tbl'><table><thead><tr><th>Status</th><th>When</th><th>Body</th></tr></thead><tbody>{''.join(resp_rows)}</tbody></table></div>"
    ok_name = ref_name(op["responses"]["200"]["content"]["application/json"]["schema"])
    req_line = request_examples.get((method, path), path)
    return f"""
    <section class="ep" id="{slug(method, path)}">
      <h3><span class="m m-{method}">{method.upper()}</span><code class="path">{E(path)}</code></h3>
      <p class="lede">{md(op.get('description') or op.get('summary', ''))}</p>
      <p class="used"><span class="lbl">Dashboard uses it for</span> {E(PANEL[path])}</p>
      {ptable}{body}{resp}
      <h4>Example: <code>{E(method.upper())} {E(req_line)}</code> → 200 <a class="sref" href="#schema-{ok_name}">{ok_name}</a></h4>
      {code(examples[(method, path)])}
    </section>"""


endpoints_html = "".join(endpoint(m, p) for m, p in ORDER)

# Schemas in reading order: what each endpoint returns, then the rest.
schema_order = []
for m, p in ORDER:
    op = spec["paths"][p][m]
    for n in ([ref_name(op["requestBody"]["content"]["application/json"]["schema"])] if "requestBody" in op else []) + \
             [ref_name(op["responses"]["200"]["content"]["application/json"]["schema"])]:
        for x in schemas_used(n):
            if x not in schema_order:
                schema_order.append(x)
for n in ["HealthDown", "ErrorOut", "HTTPValidationError", "ValidationError"]:
    if n not in schema_order:
        schema_order.append(n)
schemas_html = "".join(
    f"<section class='sch' id='schema-{n}'><h3><code>{n}</code></h3>"
    f"{('<p>' + E(SCHEMAS[n]['description']) + '</p>') if SCHEMAS[n].get('description') else ''}{schema_table(n)}</section>"
    for n in schema_order)

issue_rows = "".join(
    f"<tr><td><code>{k}</code></td><td>{E(v)}</td><td>{E(src)}</td></tr>" for k, v, src in [
        (k, v, "Dropdown “" + next(d for d, i in analytics.DROPDOWN_TO_ISSUE.items() if i == k) + "” or a comment"
         if k in analytics.DROPDOWN_TO_ISSUE.values() else
         ("A comment with no readable reason" if k == "unclear" else
          "A comment that couldn't be processed, or that the pipeline skipped" if k == "failed" else "Comment only"))
        for k, v in analytics.ISSUES.items()])

nav = "".join(f"<a href='#{slug(m, p)}'><span class='m m-{m}'>{m.upper()}</span>{E(p)}</a>" for m, p in ORDER)

page = f"""<meta charset="utf-8">
<title>Return Pulse API</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root {{
  --page: #f6f7f9; --surface: #ffffff; --ink: #161a22; --ink-2: #444b58; --muted: #687080;
  --rule: #e2e5eb; --code-bg: #f1f3f7; --accent: #2b50c8; --accent-wash: #e8edfc;
  --get-bg: #e6efff; --get-ink: #1f47b8; --post-bg: #fff0d9; --post-ink: #8a5300;
  --ok: #0f7a3a; --warn: #8a5300; --bad: #b42323; --note-bg: #fff6e3; --note-rule: #e5b65a;
  --sans: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --page: #111318; --surface: #181b22; --ink: #eceef3; --ink-2: #c3c8d2; --muted: #98a0ae;
    --rule: #2a2f39; --code-bg: #1f232c; --accent: #8ea8ff; --accent-wash: #1e2742;
    --get-bg: #1c2a4d; --get-ink: #a9c0ff; --post-bg: #3a2b10; --post-ink: #f3c27a;
    --ok: #5fd08c; --warn: #f3c27a; --bad: #ff8f8f; --note-bg: #2a2412; --note-rule: #8a6a26;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --page: #111318; --surface: #181b22; --ink: #eceef3; --ink-2: #c3c8d2; --muted: #98a0ae;
  --rule: #2a2f39; --code-bg: #1f232c; --accent: #8ea8ff; --accent-wash: #1e2742;
  --get-bg: #1c2a4d; --get-ink: #a9c0ff; --post-bg: #3a2b10; --post-ink: #f3c27a;
  --ok: #5fd08c; --warn: #f3c27a; --bad: #ff8f8f; --note-bg: #2a2412; --note-rule: #8a6a26;
}}
* {{ box-sizing: border-box; }}
body {{ background: var(--page); color: var(--ink); font: 15px/1.6 var(--sans); margin: 0; }}
a {{ color: var(--accent); text-underline-offset: 2px; }}
code, pre {{ font-family: var(--mono); font-size: 0.86em; }}
.shell {{ max-width: 1180px; margin: 0 auto; padding-inline: 20px; padding-block: 28px 64px;
  display: grid; grid-template-columns: 230px minmax(0, 1fr); gap: 40px; }}
nav.toc {{ position: sticky; top: calc(env(safe-area-inset-top, 0px) + 20px); align-self: start;
  display: flex; flex-direction: column; gap: 2px; font-size: 13.5px; }}
nav.toc .grp {{ color: var(--muted); font-size: 11.5px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; margin: 14px 0 4px; }}
nav.toc a {{ color: var(--ink-2); text-decoration: none; padding: 4px 8px; border-radius: 6px; display: flex; gap: 8px; align-items: center; font-family: var(--mono); font-size: 12.5px; }}
nav.toc a.plain {{ font-family: var(--sans); font-size: 13.5px; }}
nav.toc a:hover, nav.toc a:focus-visible {{ background: var(--accent-wash); color: var(--ink); }}
main {{ min-width: 0; }}
header.top .eyebrow {{ color: var(--muted); font-size: 13px; letter-spacing: .02em; }}
h1 {{ font-size: 34px; line-height: 1.15; margin: 4px 0 10px; font-weight: 600; letter-spacing: -0.01em; text-wrap: balance; }}
h2 {{ font-size: 22px; margin: 48px 0 6px; padding-top: 18px; border-top: 1px solid var(--rule); text-wrap: balance; }}
h3 {{ font-size: 18px; margin: 0 0 6px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }}
h4 {{ font-size: 13px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); margin: 20px 0 8px; font-weight: 600; }}
h4 code {{ text-transform: none; letter-spacing: 0; color: var(--ink-2); }}
p {{ max-width: 70ch; margin: 0 0 12px; }}
.intro {{ font-size: 16.5px; color: var(--ink-2); max-width: 66ch; }}
.facts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 230px), 1fr)); gap: 12px; margin: 22px 0 6px; }}
.facts div {{ background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; padding: 12px 14px; }}
.facts b {{ display: block; font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; font-weight: 600; margin-bottom: 2px; }}
.audience {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr)); gap: 12px; margin-top: 14px; }}
.audience a {{ display: block; background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; padding: 14px 16px; color: var(--ink); text-decoration: none; }}
.audience a:hover {{ border-color: var(--accent); }}
.audience strong {{ display: block; margin-bottom: 2px; }}
.audience span {{ color: var(--ink-2); font-size: 14px; }}
.m {{ font-family: var(--mono); font-size: 11.5px; font-weight: 500; padding: 2px 7px; border-radius: 5px; letter-spacing: .03em; }}
.m-get {{ background: var(--get-bg); color: var(--get-ink); }}
.m-post {{ background: var(--post-bg); color: var(--post-ink); }}
.path {{ font-size: 17px; font-weight: 500; color: var(--ink); }}
.ep, .sch, .card {{ background: var(--surface); border: 1px solid var(--rule); border-radius: 12px; padding: 20px 22px; margin-top: 16px; }}
.lede {{ color: var(--ink-2); }}
.used {{ font-size: 14px; }}
.lbl {{ font-size: 11.5px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; margin-right: 6px; }}
.tbl {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th {{ text-align: left; font-size: 12px; color: var(--muted); font-weight: 600; padding: 6px 10px; border-bottom: 1px solid var(--rule); white-space: nowrap; }}
td {{ padding: 8px 10px; border-bottom: 1px solid var(--rule); vertical-align: top; }}
tr:last-child td {{ border-bottom: 0; }}
td .f {{ color: var(--ink); font-weight: 500; white-space: nowrap; }}
td.t {{ color: var(--ink-2); font-size: 13px; min-width: 120px; }}
td.t code, td code {{ background: var(--code-bg); padding: 1px 5px; border-radius: 4px; }}
.opt {{ display: block; font-size: 11.5px; color: var(--muted); }}
.ex {{ color: var(--muted); font-size: 13px; }}
.st {{ font-family: var(--mono); font-size: 12.5px; font-weight: 500; }}
.st2 {{ color: var(--ok); }} .st4 {{ color: var(--warn); }} .st5 {{ color: var(--bad); }}
pre.code {{ background: var(--code-bg); border-radius: 8px; padding: 14px 16px; overflow-x: auto; margin: 0; line-height: 1.5; max-height: 460px; overflow-y: auto; }}
.sref {{ font-family: var(--mono); font-size: 12.5px; text-transform: none; letter-spacing: 0; }}
.note {{ background: var(--note-bg); border-left: 3px solid var(--note-rule); border-radius: 8px; padding: 12px 14px; margin: 14px 0; max-width: 76ch; }}
ol.rules, ul.rules {{ padding-left: 20px; max-width: 76ch; display: grid; gap: 8px; margin: 10px 0; }}
.copy {{ font: 500 13px var(--sans); background: var(--accent); color: #fff; border: 0; border-radius: 8px; padding: 8px 14px; cursor: pointer; }}
:root[data-theme="dark"] .copy {{ color: #0d1020; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) .copy {{ color: #0d1020; }} }}
.copy:focus-visible, a:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
.copy-status {{ font-size: 13px; color: var(--muted); margin-left: 8px; }}
footer {{ color: var(--muted); font-size: 13px; margin-top: 40px; }}
@media (max-width: 900px) {{
  .shell {{ grid-template-columns: 1fr; gap: 0; padding-inline: 16px; }}
  nav.toc {{ position: static; flex-direction: row; flex-wrap: wrap; gap: 4px; margin-bottom: 8px; }}
  nav.toc .grp {{ flex-basis: 100%; margin: 6px 0 0; }}
  h1 {{ font-size: 27px; }}
  .ep, .sch, .card {{ padding: 16px; }}
}}
</style>

<div class="shell">
  <nav class="toc" aria-label="Contents">
    <div class="grp">Start</div>
    <a class="plain" href="#conventions">Conventions</a>
    <a class="plain" href="#issues">Issue types</a>
    <div class="grp">Endpoints</div>
    {nav}
    <div class="grp">LLM pipeline</div>
    <a class="plain" href="#pipeline">Write contract</a>
    <a class="plain" href="#feedback">Neha's corrections</a>
    <div class="grp">Reference</div>
    <a class="plain" href="#schemas">All schemas</a>
    <a class="plain" href="#openapi">OpenAPI file</a>
  </nav>

  <main>
    <header class="top">
      <div class="eyebrow">Dhaga &amp; Co. · Return Pulse</div>
      <h1>Return Pulse API</h1>
      <p class="intro">Every API behind the returns dashboard Neha (Category Head) uses to see why products come back.
        It reads every return, including the free-text “Other” comments, and shows which vendor, product type, size and
        city to fix first. This page is generated from the running code, so the schemas and examples are exact.</p>
    </header>

    <div class="audience">
      <a href="#get-api-summary"><strong>Services team</strong><span>Six HTTP endpoints, JSON in and out, with the
        schema, errors and a real example for each.</span></a>
      <a href="#pipeline"><strong>LLM team</strong><span>The pipeline doesn't call the API: it writes rows to
        <code>classified_returns</code>. The rules for those rows, and the correction data you get back.</span></a>
    </div>

    <h2 id="conventions">Conventions</h2>
    <div class="facts">
      <div><b>Base URL</b><code>http://localhost:8000</code><br>The hosted dashboard at <a href="https://r4mkum4r-return-pulse.static.hf.space">r4mkum4r-return-pulse.static.hf.space</a> is a static site with no API: it serves the same JSON from <code>sample/*.json</code>.</div>
      <div><b>Format</b>JSON, UTF-8. Comments may contain Hinglish, Devanagari and emoji.</div>
      <div><b>Auth</b>None. Internal tool; the only write is a correction.</div>
      <div><b>Time window</b>Every count covers the latest twelve whole months in the data (<code>meta.window</code>).</div>
    </div>
    <ul class="rules">
      <li><strong>Errors</strong> return a non-2xx status with <code>{{"detail": "…"}}</code>. The sentence is written to be
        shown on screen as is. 503 means the database can't be reached or is empty; the password is never included.</li>
      <li><strong>Strict shapes.</strong> Every response is validated against its schema before it leaves the server, and
        unknown fields are refused.</li>
      <li><strong>Customer text is untrusted.</strong> <code>comment</code> is what the customer typed. Escape it before
        rendering anywhere.</li>
      <li><strong>Shares</strong> are fractions from 0 to 1, rounded to four places. Counts are whole numbers.</li>
      <li><strong>All data is synthetic</strong> (<code>meta.synthetic: true</code>). It is shaped like Dhaga's real returns:
        about 45% of returns are “Other”, written in Hinglish with typos.</li>
    </ul>

    <h2 id="issues">Issue types</h2>
    <p>One list, used everywhere: <code>issue_type</code> in responses, <code>classified_returns.issue_type</code> written by the
      pipeline, and <code>true_issue</code> in the evaluation set. A value outside this list is shown as <code>failed</code>.</p>
    <div class="card tbl"><table><thead><tr><th>Value</th><th>Label on screen</th><th>Comes from</th></tr></thead>
      <tbody>{issue_rows}</tbody></table></div>

    <h2 id="endpoints">Endpoints</h2>
    {endpoints_html}

    <h2 id="pipeline">For the LLM team: the write contract</h2>
    <p>The classifier pipeline never calls this API. It writes one row per return to the <code>classified_returns</code>
      table (schema in <code>sql/01_schema.sql</code>). The API reads that table, recomputes every number, and the dashboard
      switches from the keyword stand-in to your labels. No deploy and no restart are needed.</p>
    <div class="card tbl"><table><thead><tr><th>Column</th><th>Type</th><th>Rule</th></tr></thead><tbody>
      <tr><td><code class="f">return_id</code></td><td class="t">text, primary key</td><td>From <code>returns.return_id</code>. One row per return; write again to replace.</td></tr>
      <tr><td><code class="f">issue_type</code></td><td class="t">text, required</td><td>One of the <a href="#issues">issue types</a>. Use <code>unclear</code> when the comment gives no reason. Use <code>failed</code> when your output didn't validate after the retry. Never guess.</td></tr>
      <tr><td><code class="f">confidence</code></td><td class="t">numeric 0–1</td><td>Null for <code>gate</code> and <code>failed</code>. Below <code>settings.confidence_threshold</code> ({analytics.DEFAULT_SETTINGS['confidence_threshold']:.2f}) the row should go to the strong model for a second opinion.</td></tr>
      <tr><td><code class="f">evidence_phrase</code></td><td class="t">text</td><td>The words the label is based on. It must be an exact substring of <code>returns.other_text</code> (case-insensitive), or the dashboard won't highlight it.</td></tr>
      <tr><td><code class="f">source</code></td><td class="t">text, required</td><td><code>gate</code> (empty or junk text, no model called), <code>cheap_model</code>, <code>strong_model</code>.</td></tr>
      <tr><td><code class="f">model_name</code></td><td class="t">text</td><td>The model id that produced the label. Null for <code>gate</code>.</td></tr>
      <tr><td><code class="f">classified_at</code></td><td class="t">timestamptz</td><td>Defaults to now. The API watches the row count and the latest timestamp to know when to recompute.</td></tr>
    </tbody></table></div>
    <ol class="rules">
      <li><strong>Write rows only for “Other” returns.</strong> Returns with a dropdown reason are labelled by code and never need a model.</li>
      <li><strong>Cover every “Other” return in the window.</strong> Once the table has any rows, an “Other” return without one is
        shown as <code>failed</code>: “Not read by the AI yet”. Gaps are visible, not hidden.</li>
      <li><strong>Unknown values fail visibly.</strong> An <code>issue_type</code> outside the list, for example <code>fabric_quality</code>,
        appears as <code>failed</code> with the message “The AI returned an unknown issue type”.</li>
      <li><strong>Never put <code>eval_return_labels</code> in a prompt.</strong> It is the answer sheet for measuring accuracy. The app doesn't load it and it isn't deployed.</li>
    </ol>
    <p>What a labelled comment looks like on screen (a real row; today it comes from the keyword stand-in):</p>
    {code({k: sample_comment[k] for k in ("return_id", "comment", "issue_type", "confidence", "evidence_phrase", "source", "model_name")})}

    <h2 id="feedback">Neha's corrections, for improving the classifier</h2>
    <p>Every ✓ / ✗ Neha clicks is stored in the <code>corrections</code> table and served by
      <a href="#get-api-corrections"><code>GET /api/corrections</code></a> (latest mark per return). A ✗ with a
      <code>corrected_issue</code> is a hand-checked label: use these rows to find prompt failures. Rows are appended; the latest
      row per <code>return_id</code> is the current mark.</p>
    <div class="note">Who decides what accuracy is good enough to act on a vendor is still an open question for Neha. Until she
      sets it, show the measured accuracy, not a pass/fail verdict.</div>

    <h2 id="schemas">All schemas</h2>
    <p>Every shape that crosses the API, in the order the endpoints use them. Source: <code>app/schemas.py</code>.</p>
    {schemas_html}

    <h2 id="openapi">OpenAPI file</h2>
    <p>The machine-readable spec (OpenAPI {E(spec['openapi'])}) for generating clients or contract tests. It is published
      alongside this page as <code>openapi.json</code>, is in the repo at <code>docs/api/openapi.json</code>, and is served
      live by the app at <code>/api/openapi.json</code>, with interactive docs at <code>/api/docs</code>.</p>
    <p><button class="copy" id="copy-spec" type="button">Copy OpenAPI JSON</button><span class="copy-status" id="copy-status" aria-live="polite"></span></p>
    <pre class="code" id="spec-fallback" hidden></pre>

    <footer>Generated by <code>scripts/build_api_reference.py</code> from API version {E(spec['info']['version'])}. Examples are real responses from the synthetic data.</footer>
  </main>
</div>
<script>
document.getElementById("copy-spec").addEventListener("click", async () => {{
  const status = document.getElementById("copy-status");
  let text;
  try {{
    const res = await fetch("openapi.json");
    if (!res.ok) throw new Error("status " + res.status);
    text = JSON.stringify(await res.json(), null, 2);
  }} catch (e) {{
    status.textContent = "Couldn't load openapi.json (" + e.message + "). Use docs/api/openapi.json in the repo.";
    return;
  }}
  try {{
    await navigator.clipboard.writeText(text);
    status.textContent = "Copied.";
  }} catch (_) {{
    const pre = document.getElementById("spec-fallback");
    pre.textContent = text;
    pre.hidden = false;
    const r = document.createRange(); r.selectNodeContents(pre);
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
    status.textContent = "Clipboard blocked here: the spec is shown and selected below. Press Cmd/Ctrl+C.";
  }}
}});
</script>
"""

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "openapi.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
(OUT / "index.html").write_text(page, encoding="utf-8")
print(f"Wrote {OUT / 'index.html'} ({len(page) // 1024} KB) and openapi.json: "
      f"{len(spec['paths'])} paths, {len(schema_order)} schemas documented")
