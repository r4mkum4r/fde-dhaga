import { DATA_MODE, getSummary, getTrend, getReturns, getCorrections, saveCorrection } from "./api.js";
import { BRIEF, OPEN_QUESTIONS } from "./brief.js";

const ISSUE_LABELS = {
  too_small: "Too small", too_large: "Too large", colour_mismatch: "Colour mismatch",
  quality: "Quality", damaged: "Damaged", wrong_item: "Wrong item",
  changed_mind: "Changed mind", delivery_late: "Delivery late",
  unclear: "Unclear", failed: "Failed",
};
const NOT_CLASSIFIED = ["unclear", "failed"];
const SOURCE_LABELS = {
  dropdown: "customer picked this in the app",
  gate: "comment too short to read",
  sample_stub: "sorted by keyword match (temporary, before the AI reading)",
  sample_stub_low_conf: "sorted by keyword match, unsure (temporary, before the AI reading)",
  cheap_model: "read by AI",
  strong_model: "read by AI, then double-checked by a second AI",
  pipeline: "not read yet",
  llm: "read by AI",
};
// Plain-language sentences for "What to fix first". Suggestions only; Neha decides.
const ISSUE_PHRASE = {
  too_small: "run small", too_large: "run large", colour_mismatch: "don't match their photos",
  quality: "draw quality complaints", damaged: "arrive damaged", wrong_item: "get the wrong item sent",
  changed_mind: "are often sent back as “changed my mind”", delivery_late: "arrive late",
};
const NEXT_STEP = {
  too_small: "Check this vendor's size chart against a physical sample",
  too_large: "Check this vendor's size chart against a physical sample",
  colour_mismatch: "Re-check the product photos against the physical sample",
  quality: "Raise fabric and stitching with the vendor and inspect the next batch",
  damaged: "Check packaging and courier handling on these orders",
  wrong_item: "Check picking and labelling at the fulfilment centre",
  delivery_late: "Check courier lead times on this route",
  changed_mind: "Check that the listing's photos and copy set the right expectation",
};
const PLURAL = (sub) => {
  const w = sub.toLowerCase();
  if (/(ss|sh|ch|x)$/.test(w)) return `${w}es`;
  return /s$/.test(w) ? w : `${w}s`;
};
// Only these are a location's problem; fit, colour and quality belong to the product.
const LOCATION_ISSUES = ["damaged", "delivery_late", "wrong_item"];
const tag = (kind, title) => {
  // A brief tag shows its section (e.g. "Brief §05") so the source is readable without hovering.
  const section = kind === "brief" && title ? (title.match(/§\d+/) || [""])[0] : "";
  const text = { brief: "Brief", calc: "Calc", est: "Estimate", sample: "Test data" }[kind] + (section ? ` ${section}` : "");
  return `<span class="src ${kind}" title="${esc(title || "")}">${esc(text)}</span>`;
};
const TABLE_PREVIEW = 10;

const state = {
  summary: null,
  trendIssue: "too_small",
  corrections: {},
  accuracy: { reviewed: 0, correct: 0 },
  showAllHotspots: false,
  showAllLocations: false,
};

// ---------- helpers ----------
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const int = (n) => Number(n).toLocaleString("en-IN");
const pct = (x, digits = 0) => `${(x * 100).toFixed(digits)}%`;
const labelOf = (issue) => ISSUE_LABELS[issue] || issue;

function statusBadge(issue) {
  if (issue === "unclear") return `<span class="badge unclear"><span class="ico" aria-hidden="true">?</span>Unclear</span>`;
  if (issue === "failed") return `<span class="badge failed"><span class="ico" aria-hidden="true">✕</span>Failed</span>`;
  return `<span class="badge">${esc(labelOf(issue))}</span>`;
}

function errorBox(title, err) {
  const detail = err && err.detail ? `<div><code>${esc(err.detail)}</code></div>` : "";
  return `<div class="error-box" role="alert"><strong>${esc(title)}</strong>
    <div>${esc(err ? err.message : "")}</div>${detail}
    <div style="margin-top:6px">Nothing on this part of the page is shown until it loads, so you never see stale or made-up numbers.</div></div>`;
}

function shareCell(share, baseline) {
  const w = Math.min(100, share * 100);
  const b = Math.min(100, baseline * 100);
  return `<div class="share" title="${pct(share)} here, against ${pct(baseline)} across all returns">
    <span class="pct">${pct(share)}</span>
    <span class="meter" aria-hidden="true"><span class="fill" style="width:${w}%"></span><span class="norm" style="left:calc(${b}% - 1px)"></span></span>
  </div>`;
}

// ---------- 0. what to fix first ----------
function renderFixFirst() {
  const { hotspots, locations } = state.summary;
  const items = [];
  // Merge a vendor's product types that share the same top reason into one line.
  // Hotspots arrive ranked; a vendor's line keeps its two strongest product types and counts the rest.
  const seen = new Map();
  for (const h of hotspots) {
    const key = `${h.vendor_id}|${h.top_issue}`;
    if (seen.has(key)) { seen.get(key).all.push(h); continue; }
    if (seen.size >= 3) continue;
    const item = { kind: "vendor", issue: h.top_issue, all: [h] };
    seen.set(key, item);
    items.push(item);
  }
  items.forEach((it) => { it.spots = it.all.slice(0, 2); it.more = it.all.length - it.spots.length; });
  const place = locations.find((l) => LOCATION_ISSUES.includes(l.top_issue));
  if (place) items.push({ kind: "place", issue: place.top_issue, place });

  if (!items.length) {
    $("fix-list").innerHTML = `<li class="none">Nothing stands out yet: no vendor, product or city is at least 5 points above the usual mix.</li>`;
    return;
  }

  $("fix-list").innerHTML = items.map((it, i) => {
    if (it.kind === "place") {
      const l = it.place;
      return `<li>
        <div class="headline">${esc(l.city)}: parcels ${esc(ISSUE_PHRASE[l.top_issue] || labelOf(l.top_issue).toLowerCase())} far more often than elsewhere</div>
        <div class="evidence">${tag("sample")}${pct(l.top_issue_share)} of ${int(l.returns)} returns from ${esc(l.city)} are ${esc(labelOf(l.top_issue).toLowerCase())}, against ${pct(l.baseline_share)} across all returns.</div>
        <div class="next"><span><strong>Suggestions:</strong> ${esc(NEXT_STEP[l.top_issue] || "Read the comments")}.</span>
          <span class="who">Not a catalogue fix: share with Faizan (Supply Chain).</span>
          <button class="link" data-fix="${i}">Read the comments</button></div>
      </li>`;
    }
    const first = it.spots[0];
    const products = it.spots.map((h) => PLURAL(h.subcategory)).join(" and ") + (it.more ? ` (and ${it.more} more product type${it.more > 1 ? "s" : ""})` : "");
    const sizes = [...new Set(it.spots.flatMap((h) => h.size === "All sizes" ? [] : h.size.split(", ")))];
    const sizeText = sizes.length && (it.issue === "too_small" || it.issue === "too_large") ? `, mostly in ${sizes.join(", ")}` : "";
    const ev = it.spots.map((h) =>
      `${pct(h.top_issue_share)} of ${int(h.returns)} ${esc(PLURAL(h.subcategory))} returns`).join("; ");
    return `<li>
      <div class="headline">${esc(first.vendor_name)} (${esc(first.vendor_id)}): ${esc(products)} ${esc(ISSUE_PHRASE[it.issue] || labelOf(it.issue).toLowerCase())}${esc(sizeText)}</div>
      <div class="evidence">${tag("sample")}${ev} are ${esc(labelOf(it.issue).toLowerCase())}, against ${pct(first.baseline_share)} across all returns.</div>
      <div class="next"><span><strong>Suggestions:</strong> ${esc(NEXT_STEP[it.issue] || "Read the comments")}.</span>
        <span class="who">Neha decides.</span>
        <button class="link" data-fix="${i}">Read the comments</button></div>
    </li>`;
  }).join("");

  $("fix-list").querySelectorAll("[data-fix]").forEach((b) => b.addEventListener("click", () => {
    const it = items[+b.dataset.fix];
    if (it.kind === "place") {
      const l = it.place;
      openDrawer({ title: `${l.city}, ${l.state}`, sub: `${int(l.returns)} returns · ${pct(l.top_issue_share)} ${labelOf(l.top_issue).toLowerCase()}, against ${pct(l.baseline_share)} overall`,
        filters: { city: l.city }, focusIssue: l.top_issue });
    } else {
      openHotspotDrawer(it.spots[0]);
    }
  }));
}

// ---------- 0b. at Dhaga's scale ----------
function renderScale() {
  const h = state.summary.headline;
  const orders = BRIEF.ordersPerWeek.value;
  const returns = orders * BRIEF.returnRate.value;
  const other = returns * BRIEF.otherShare.value;
  const value = returns * BRIEF.avgOrderValue.value;
  // Share of "Other" comments that got a reason in this data (unclear and failed only come from "Other").
  const readRate = h.other_comments ? 1 - (h.unclear + h.failed) / h.other_comments : null;
  const rateSource = state.summary.meta.data_mode === "sample" ? "the keyword match" : "the AI reading";
  const crore = (n) => `₹${(n / 1e7).toFixed(2)} crore`;

  const cells = [
    { k: "Orders a week", v: int(orders), how: `${tag("brief", BRIEF.ordersPerWeek.cite)}given by Dhaga` },
    { k: "Returns a week", v: int(Math.round(returns)),
      how: `${tag("calc")}${int(orders)} orders × ${pct(BRIEF.returnRate.value)} return rate ${tag("brief", BRIEF.returnRate.cite)}` },
    { k: "“Other” comments a week, unread today", v: int(Math.round(other)), warn: true,
      how: `${tag("calc")}${int(Math.round(returns))} returns × ${pct(BRIEF.otherShare.value)} in “Other” ${tag("brief", BRIEF.otherShare.cite)}<br>
        Neha reads ${esc(BRIEF.nehaReadsPerSitting.text)} ${tag("brief", BRIEF.nehaReadsPerSitting.cite)}` },
    { k: "Order value sent back a week", v: crore(value),
      how: `${tag("calc")}${int(Math.round(returns))} returns × ₹${int(BRIEF.avgOrderValue.value)} average order ${tag("brief", BRIEF.avgOrderValue.cite)}<br>
        This is order value, not loss. The cost of a return isn't in the brief (see below).` },
  ];
  if (readRate != null) {
    cells.push({ k: "“Other” comments that would get a reason", v: `≈ ${int(Math.round(other * readRate))}`,
      how: `${tag("est")}${int(Math.round(other))} × ${pct(readRate)} read by ${esc(rateSource)} ${tag("sample", "Measured on the test data")}` });
    cells.push({ k: "Still needing a human read", v: `≈ ${int(Math.round(other * (1 - readRate)))}`, warn: true,
      how: `${tag("est")}the unclear and failed share, shown in full below, never guessed` });
  }
  $("scale").innerHTML = cells.map((c) => `<div class="cell ${c.warn ? "warn" : ""}">
    <div class="k">${esc(c.k)}</div><div class="v">${c.v}</div><div class="how">${c.how}</div></div>`).join("");
  const cites = [BRIEF.ordersPerWeek, BRIEF.returnRate, BRIEF.otherShare, BRIEF.avgOrderValue, BRIEF.nehaReadsPerSitting]
    .map((b) => b.cite);
  $("scale-sources").innerHTML = [...new Set(cites)].map((c) => `<li>${esc(c)}</li>`).join("");
}

function renderOpenQuestions() {
  $("open-q").innerHTML = OPEN_QUESTIONS.map((o) =>
    `<li>${esc(o.q)} <span class="who">Ask: ${esc(o.owner)}</span></li>`).join("");
}

// ---------- 1. headline tiles ----------
function renderTiles() {
  const h = state.summary.headline;
  const a = state.accuracy;
  const accValue = a.reviewed ? pct(a.correct / a.reviewed) : "—";
  const accNote = state.correctionsError
    ? `Couldn't load your earlier marks (${state.correctionsError}). New marks still save.`
    : a.reviewed
      ? `${int(a.correct)} of ${int(a.reviewed)} labels you checked were right`
      : "No labels checked yet. Open a problem spot and mark ✓ or ✗.";
  $("tiles").innerHTML = `
    <div class="tile">
      <div class="label">${tag("sample", "Counted on the synthetic test data, not Dhaga's real volume")}Returns analysed</div>
      <div class="value hero">${int(h.returns)}</div>
      <div class="note">${esc(state.summary.meta.window.label)}</div>
    </div>
    <div class="tile">
      <div class="label">${tag("sample", "Measured on the synthetic test data")}Returns with a known reason</div>
      <div class="value">${pct(h.known_reason_before)}<span class="arrow">→</span>${pct(h.known_reason_after)}</div>
      <div class="note">Dropdown alone, then after reading ${int(h.other_comments)} “Other” comments</div>
    </div>
    <div class="tile alert">
      <div class="label">${tag("sample", "Measured on the synthetic test data")}Couldn't classify</div>
      <div class="split">
        <div>${statusBadge("unclear")}<span class="value">${int(h.unclear)}</span>
          <button class="link" data-open="unclear">Read them</button></div>
        <div>${statusBadge("failed")}<span class="value">${int(h.failed)}</span>
          <button class="link" data-open="failed">See which</button></div>
      </div>
    </div>
    <div class="tile">
      <div class="label">Label accuracy, from your checks</div>
      <div class="value" id="acc-value">${accValue}</div>
      <div class="note" id="acc-note">${esc(accNote)}</div>
      <div class="note">Good enough? Not agreed yet. Neha sets the bar (see below).</div>
    </div>`;
  $("tiles").querySelectorAll("[data-open]").forEach((b) =>
    b.addEventListener("click", () => openIssueDrawer(b.dataset.open)));
}

function refreshAccuracy() {
  const a = state.accuracy;
  if ($("acc-value")) {
    $("acc-value").textContent = a.reviewed ? pct(a.correct / a.reviewed) : "—";
    $("acc-note").textContent = a.reviewed
      ? `${int(a.correct)} of ${int(a.reviewed)} labels you checked were right`
      : "No labels checked yet. Open a problem spot and mark ✓ or ✗.";
  }
  const foot = $("drawer-foot");
  if (foot && !$("drawer").hidden) foot.innerHTML = drawerFootText();
}

// ---------- 1b. return drivers bar chart ----------
function renderDrivers() {
  const box = $("drivers-chart");
  const drivers = state.summary.drivers;
  const known = drivers.filter((d) => !NOT_CLASSIFIED.includes(d.issue) && d.count > 0)
    .sort((a, b) => b.count - a.count);
  const gaps = drivers.filter((d) => NOT_CLASSIFIED.includes(d.issue)); // always shown, even at 0
  const rows = [...known, ...gaps];

  const W = Math.max(320, box.clientWidth || 600);
  const labelW = W < 480 ? 112 : 140;
  const valueW = 108;
  const rowH = 30, barH = 18, sepH = 40;
  const plotW = W - labelW - valueW;
  const max = Math.max(...rows.map((d) => d.count), 1);
  const H = rows.length * rowH + sepH + 4;

  let y = 0;
  const parts = [];
  rows.forEach((d, i) => {
    if (i === known.length) {
      parts.push(`<line class="grid-line" x1="0" x2="${W}" y1="${y + 10}" y2="${y + 10}"/>
        <text class="tick" x="0" y="${y + 32}">Couldn't classify: shown, never guessed into a reason</text>`);
      y += sepH;
    }
    const w = Math.max(d.count > 0 ? 3 : 0, (d.count / max) * plotW);
    const cls = NOT_CLASSIFIED.includes(d.issue) ? d.issue : "";
    const sel = d.issue === state.trendIssue ? "selected" : "";
    const by = y + (rowH - barH) / 2;
    const r = Math.min(4, w / 2);
    // bar: square at the baseline, 4px rounded data-end
    const path = w > 0
      ? `M${labelW},${by} h${w - r} a${r},${r} 0 0 1 ${r},${r} v${barH - 2 * r} a${r},${r} 0 0 1 -${r},${r} h-${w - r} z`
      : "";
    const icon = d.issue === "unclear" ? "? " : d.issue === "failed" ? "✕ " : "";
    parts.push(`<g class="row ${sel}" tabindex="0" role="button" data-issue="${d.issue}"
        aria-label="${esc(d.label)}: ${int(d.count)} returns, ${pct(d.share)}">
      <rect class="hit" x="0" y="${y}" width="${W}" height="${rowH}" rx="6"/>
      <text class="label" x="0" y="${y + rowH / 2 + 4}">${esc(icon + d.label)}</text>
      ${path ? `<path class="bar ${cls}" d="${path}"/>` : ""}
      <text class="value" x="${labelW + w + 8}" y="${y + rowH / 2 + 4}">${int(d.count)}
        <tspan class="tick" dx="4">${pct(d.share)}</tspan></text>
    </g>`);
    y += rowH;
  });
  parts.unshift(`<line class="baseline" x1="${labelW}" x2="${labelW}" y1="0" y2="${known.length * rowH}"/>`);

  box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Return reasons, bar chart">${parts.join("")}</svg>`;
  box.querySelectorAll(".row").forEach((g) => {
    const go = () => {
      const issue = g.dataset.issue;
      if (NOT_CLASSIFIED.includes(issue)) return openIssueDrawer(issue);
      setTrendIssue(issue);
      $("h-trend").scrollIntoView({ behavior: "smooth", block: "center" });
    };
    g.addEventListener("click", go);
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
  });
}

// ---------- 2. problem spots ----------
function renderHotspots() {
  const list = state.summary.hotspots;
  const box = $("hotspots");
  if (!list.length) {
    box.innerHTML = `<div class="empty-state">No vendor and product type has at least
      ${int(state.summary.meta.min_returns_per_hotspot)} returns with one reason standing out.</div>`;
    return;
  }
  const shown = state.showAllHotspots ? list : list.slice(0, TABLE_PREVIEW);
  box.innerHTML = `<div class="table-wrap"><table>
    <thead><tr>
      <th>Vendor</th><th>Product type</th><th>Size</th><th>Top reason</th>
      <th>Share of its returns <span class="sr-only">(line marks the usual share)</span></th>
      <th class="num">Returns</th>
    </tr></thead>
    <tbody>${shown.map((h, i) => `
      <tr class="clickable" tabindex="0" data-i="${i}" aria-label="Read comments for ${esc(h.vendor_name)} ${esc(h.subcategory)}">
        <td>${esc(h.vendor_name)} <span class="vendor-id">${esc(h.vendor_id)}</span></td>
        <td>${esc(h.subcategory)}</td>
        <td title="${esc(Object.entries(h.size_breakdown || {}).map(([k, v]) => `${k}: ${v}`).join(", "))}">${esc(h.size)}</td>
        <td>${statusBadge(h.top_issue)}</td>
        <td>${shareCell(h.top_issue_share, h.baseline_share)}</td>
        <td class="num">${int(h.returns)}</td>
      </tr>`).join("")}
    </tbody></table></div>
    <div class="table-foot">
      <span>Bar: share of this group's returns with that reason. Tick mark: the share across all returns.
        Groups need at least ${int(state.summary.meta.min_returns_per_hotspot)} returns.</span>
      ${list.length > TABLE_PREVIEW ? `<button class="link" id="hs-more">${state.showAllHotspots ? "Show top 10" : `Show all ${list.length}`}</button>` : ""}
    </div>`;
  box.querySelectorAll("tr.clickable").forEach((tr) => {
    const h = shown[+tr.dataset.i];
    const go = () => openHotspotDrawer(h);
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  });
  const more = $("hs-more");
  if (more) more.addEventListener("click", () => { state.showAllHotspots = !state.showAllHotspots; renderHotspots(); });
}

// ---------- 3. locations ----------
function renderLocations() {
  const list = state.summary.locations;
  const box = $("locations");
  if (!list.length) { box.innerHTML = `<div class="empty-state">No city stands out: every city's reasons are within 5 points of the overall mix.</div>`; return; }
  const shown = state.showAllLocations ? list : list.slice(0, TABLE_PREVIEW);
  box.innerHTML = `<div class="table-wrap"><table>
    <thead><tr><th>City</th><th>Stands out for</th><th>Share here</th><th class="num">Returns</th></tr></thead>
    <tbody>${shown.map((l, i) => `
      <tr class="clickable" tabindex="0" data-i="${i}" aria-label="Read comments from ${esc(l.city)}">
        <td title="${esc(l.state)}">${esc(l.city)}</td>
        <td>${statusBadge(l.top_issue)}</td>
        <td>${shareCell(l.top_issue_share, l.baseline_share)}</td>
        <td class="num">${int(l.returns)}</td>
      </tr>`).join("")}
    </tbody></table></div>
    <div class="table-foot">
      <span>Each city's most over-represented reason, against the share across all returns (tick mark). Only cities at least 5 points above normal are listed.</span>
      ${list.length > TABLE_PREVIEW ? `<button class="link" id="loc-more">${state.showAllLocations ? "Show top 10" : `Show all ${list.length}`}</button>` : ""}
    </div>`;
  box.querySelectorAll("tr.clickable").forEach((tr) => {
    const l = shown[+tr.dataset.i];
    const go = () => openDrawer({
      title: `${l.city}, ${l.state}`,
      sub: `${int(l.returns)} returns · stands out for ${labelOf(l.top_issue).toLowerCase()} (${pct(l.top_issue_share)} here, ${pct(l.baseline_share)} overall)`,
      filters: { city: l.city },
      focusIssue: l.top_issue,
    });
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  });
  const more = $("loc-more");
  if (more) more.addEventListener("click", () => { state.showAllLocations = !state.showAllLocations; renderLocations(); });
}

// ---------- 4. trend ----------
function setupTrendSelect() {
  const sel = $("trend-issue");
  sel.innerHTML = Object.entries(ISSUE_LABELS)
    .map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  sel.value = state.trendIssue;
  sel.addEventListener("change", () => setTrendIssue(sel.value));
}

function setTrendIssue(issue) {
  state.trendIssue = issue;
  $("trend-issue").value = issue;
  if (state.summary) renderDrivers();
  loadTrend();
}

let trendData = null;
async function loadTrend() {
  const box = $("trend-chart");
  box.innerHTML = `<div class="loading">Loading…</div>`;
  $("trend-legend").innerHTML = "";
  try {
    trendData = await getTrend(state.trendIssue);
    renderTrend();
  } catch (e) {
    trendData = null;
    box.innerHTML = errorBox("The trend didn't load.", e);
  }
}

function niceMax(v) {
  if (v <= 5) return 5;
  const p = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= v);
}

function renderTrend() {
  if (!trendData) return;
  const box = $("trend-chart");
  const { months, series } = trendData;
  const past = series.find((s) => s.key === "last_year");
  const now = series.find((s) => s.key === "this_year");
  const colours = { last_year: "var(--series-past)", this_year: "var(--series-1)" };

  const totalPast = past.counts.reduce((a, b) => a + b, 0);
  const totalNow = now.counts.reduce((a, b) => a + b, 0);
  const change = totalPast ? (totalNow - totalPast) / totalPast : null;
  const changeText = change == null ? "no returns last year to compare with"
    : `${change >= 0 ? "up" : "down"} ${pct(Math.abs(change))} on last year`;
  $("trend-legend").innerHTML = `
    <span><i class="key" style="background:${colours.last_year}"></i>${esc(past.label)}: ${int(totalPast)}</span>
    <span><i class="key" style="background:${colours.this_year}"></i>${esc(now.label)}: ${int(totalNow)}</span>
    <span>“${esc(trendData.label)}” is <strong>${esc(changeText)}</strong></span>`;

  const W = Math.max(300, box.clientWidth || 500);
  const H = 240, padL = 36, padR = 40, padT = 10, padB = 26;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const ymax = niceMax(Math.max(...past.counts, ...now.counts, 1));
  const x = (i) => padL + (i / (months.length - 1)) * plotW;
  const yy = (v) => padT + plotH - (v / ymax) * plotH;
  const ticks = [0, ymax / 2, ymax];

  const grid = ticks.map((t) => `<line class="${t === 0 ? "baseline" : "grid-line"}" x1="${padL}" x2="${W - padR}" y1="${yy(t)}" y2="${yy(t)}"/>
    <text class="tick" x="${padL - 6}" y="${yy(t) + 4}" text-anchor="end">${int(t)}</text>`).join("");
  const xlabels = months.map((m, i) => (W < 420 && i % 2) ? "" :
    `<text class="tick" x="${x(i)}" y="${H - 6}" text-anchor="middle">${esc(m)}</text>`).join("");
  const line = (s) => `<path class="line" stroke="${colours[s.key]}" d="${s.counts.map((v, i) => `${i ? "L" : "M"}${x(i)},${yy(v)}`).join(" ")}"/>`;
  const last = now.counts.length - 1;
  const endDot = `<circle class="dot" cx="${x(last)}" cy="${yy(now.counts[last])}" r="4.5" fill="${colours.this_year}"/>
    <text class="value" x="${x(last) + 8}" y="${yy(now.counts[last]) + 4}">${int(now.counts[last])}</text>`;

  box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(trendData.label)} returns per month, this year against last year">
      ${grid}${xlabels}${line(past)}${line(now)}${endDot}
      <line class="crosshair" id="xhair" y1="${padT}" y2="${padT + plotH}" style="display:none"/>
      <g id="xdots"></g>
      <rect id="trend-hit" x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="transparent"/>
    </svg><div class="tooltip" id="trend-tip"></div>`;

  // hover: crosshair + tooltip
  const svg = box.querySelector("svg");
  const tip = $("trend-tip");
  const xhair = $("xhair");
  const dots = $("xdots");
  const hide = () => { tip.style.display = "none"; xhair.style.display = "none"; dots.innerHTML = ""; };
  $("trend-hit").addEventListener("mousemove", (e) => {
    const rect = svg.getBoundingClientRect();
    const sx = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.max(0, Math.min(months.length - 1, Math.round(((sx - padL) / plotW) * (months.length - 1))));
    xhair.setAttribute("x1", x(i)); xhair.setAttribute("x2", x(i)); xhair.style.display = "";
    dots.innerHTML = [past, now].map((s) =>
      `<circle class="dot" cx="${x(i)}" cy="${yy(s.counts[i])}" r="4.5" fill="${colours[s.key]}"/>`).join("");
    tip.innerHTML = `<div class="t">${esc(months[i])}</div>` + [now, past].map((s) =>
      `<div class="r"><i class="key" style="background:${colours[s.key]}"></i>${esc(s.label)}: <strong>${int(s.counts[i])}</strong></div>`).join("");
    tip.style.display = "block";
    const px = (x(i) / W) * rect.width;
    const left = px + 14 + tip.offsetWidth > rect.width ? px - tip.offsetWidth - 14 : px + 14;
    tip.style.left = `${left}px`;
    tip.style.top = `8px`;
  });
  $("trend-hit").addEventListener("mouseleave", hide);
}

// ---------- 5. comments drawer + ✓ / ✗ ----------
const drawer = { rows: [], filter: "focus", focusIssue: null, lastFocus: null };

function openHotspotDrawer(h) {
  openDrawer({
    title: `${h.vendor_name} (${h.vendor_id}) · ${h.subcategory}`,
    sub: `${int(h.returns)} returns this year · ${pct(h.top_issue_share)} are ${labelOf(h.top_issue).toLowerCase()}`
      + ` (usual: ${pct(h.baseline_share)})` + (h.size === "All sizes" ? " · spread across sizes" : ` · mostly size ${h.size}`),
    filters: { vendor_id: h.vendor_id, subcategory: h.subcategory },
    focusIssue: h.top_issue,
  });
}

function openIssueDrawer(issue) {
  const sub = issue === "unclear"
    ? "Comments where no reason could be read. They are never guessed into a reason."
    : "Comments the AI couldn't process. They need another run, or a person to read them.";
  openDrawer({ title: `${labelOf(issue)} returns`, sub, filters: { issue_type: issue }, focusIssue: issue });
}

async function openDrawer({ title, sub, filters, focusIssue }) {
  drawer.lastFocus = document.activeElement;
  drawer.focusIssue = focusIssue;
  drawer.filter = "focus";
  $("drawer-title").textContent = title;
  $("drawer-sub").textContent = sub;
  $("drawer-filters").innerHTML = "";
  $("drawer-body").innerHTML = `<div class="loading">Loading comments…</div>`;
  $("drawer-foot").innerHTML = drawerFootText();
  $("scrim").hidden = false;
  $("drawer").hidden = false;
  $("drawer-close").focus();
  try {
    const res = await getReturns(filters);
    drawer.rows = res.returns;
    renderDrawerFilters();
    renderDrawerBody();
  } catch (e) {
    $("drawer-body").innerHTML = errorBox("The comments didn't load.", e);
  }
}

function closeDrawer() {
  $("drawer").hidden = true;
  $("scrim").hidden = true;
  if (drawer.lastFocus) drawer.lastFocus.focus();
}

function drawerFilterDefs() {
  const rows = drawer.rows;
  const comments = rows.filter((r) => r.source !== "dropdown");
  const defs = [];
  if (!NOT_CLASSIFIED.includes(drawer.focusIssue)) {
    defs.push({ key: "focus", label: `${labelOf(drawer.focusIssue)} comments`,
      test: (r) => r.source !== "dropdown" && r.issue_type === drawer.focusIssue });
  } else {
    defs.push({ key: "focus", label: labelOf(drawer.focusIssue), test: (r) => r.issue_type === drawer.focusIssue });
  }
  if (!NOT_CLASSIFIED.includes(drawer.focusIssue)) {
    defs.push({ key: "comments", label: "All comments", test: (r) => r.source !== "dropdown" });
    defs.push({ key: "unclear", label: "Unclear", test: (r) => r.issue_type === "unclear" });
    defs.push({ key: "failed", label: "Failed", test: (r) => r.issue_type === "failed" });
    defs.push({ key: "dropdown", label: "Picked in the app", test: (r) => r.source === "dropdown" });
  }
  return defs.map((d) => ({ ...d, count: (d.key === "comments" ? comments : rows).filter(d.test).length }));
}

function renderDrawerFilters() {
  const defs = drawerFilterDefs();
  // If the focused view is empty (e.g. a hotspot driven by dropdown picks), start on all comments.
  if (drawer.filter === "focus" && defs[0].count === 0 && defs.length > 1) drawer.filter = defs[1].count ? "comments" : "dropdown";
  $("drawer-filters").innerHTML = defs.map((d) =>
    `<button class="chip" data-f="${d.key}" aria-pressed="${d.key === drawer.filter}">${esc(d.label)} (${int(d.count)})</button>`).join("");
  $("drawer-filters").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    drawer.filter = b.dataset.f;
    renderDrawerFilters();
    renderDrawerBody();
  }));
}

function highlight(text, phrase) {
  if (!text) return "";
  if (!phrase) return esc(text);
  const i = text.toLowerCase().indexOf(phrase.toLowerCase());
  if (i < 0) return esc(text);
  return esc(text.slice(0, i)) + `<mark>${esc(text.slice(i, i + phrase.length))}</mark>` + esc(text.slice(i + phrase.length));
}

function returnCard(r) {
  const mark = state.corrections[r.return_id];
  const judgeable = r.source !== "dropdown" && r.issue_type !== "failed";
  const conf = r.confidence != null && r.source !== "dropdown" ? ` · ${pct(Number(r.confidence))} sure` : "";
  const why = r.source === "dropdown"
    ? `Customer picked “${esc(r.reason_dropdown)}” in the app`
    : `${esc(SOURCE_LABELS[r.source] || r.source)}${r.source === "llm" && r.model_name ? ` (${esc(r.model_name)})` : ""}${conf}` + (r.evidence_phrase ? ` · based on “${esc(r.evidence_phrase)}”` : "");
  const comment = r.comment
    ? `<div class="comment" lang="hi-Latn">${highlight(r.comment, r.evidence_phrase)}</div>`
    : `<div class="comment empty">${r.source === "dropdown" ? "No comment: reason picked from the dropdown." : "Empty comment."}</div>`;
  const verdict = judgeable ? `
    <div class="verdict" role="group" aria-labelledby="q-${esc(r.return_id)}">
      <span class="ask" id="q-${esc(r.return_id)}">Is “${esc(labelOf(r.issue_type))}” right?</span>
      <button class="yes" data-v="yes" aria-pressed="${mark?.is_correct === true}">✓ Right</button>
      <button class="no" data-v="no" aria-pressed="${mark?.is_correct === false}">✗ Wrong</button>
      ${mark?.is_correct === false ? correctionSelect(r, mark) : ""}
      <span class="saved" aria-live="polite"></span>
    </div>` : "";
  const cls = r.issue_type === "unclear" ? "is-unclear" : r.issue_type === "failed" ? "is-failed" : "";
  return `<article class="ret ${cls}" data-id="${esc(r.return_id)}">
    <div class="meta"><span>${esc(r.return_date)}</span><span>${esc(r.city)}</span>
      <span>${esc(r.product_name)} · size ${esc(r.size)}</span><span>${esc(r.vendor_id)}</span><span>${esc(r.return_id)}</span></div>
    ${comment}
    <div class="label-row">${statusBadge(r.issue_type)}<span class="why">${why}</span>${verdict}</div>
    ${r.explanation ? `<div class="explain"><span class="lbl">AI's reason</span> ${esc(r.explanation)}</div>` : ""}
    ${r.error ? `<div class="error">✕ ${esc(r.error)}</div>` : ""}
  </article>`;
}

function correctionSelect(r, mark) {
  const opts = Object.entries(ISSUE_LABELS).filter(([k]) => k !== "failed" && k !== r.issue_type)
    .map(([k, v]) => `<option value="${k}" ${mark?.corrected_issue === k ? "selected" : ""}>${esc(v)}</option>`).join("");
  return `<select class="control fix" aria-label="What should the label be?">
    <option value="">What should it be? (optional)</option>${opts}</select>`;
}

const PAGE = 60;
function renderDrawerBody(limit = PAGE) {
  const def = drawerFilterDefs().find((d) => d.key === drawer.filter);
  const rows = drawer.rows.filter(def.test);
  const body = $("drawer-body");
  if (!rows.length) {
    body.innerHTML = `<div class="empty-state">Nothing here.</div>`;
    return;
  }
  body.innerHTML = rows.slice(0, limit).map(returnCard).join("")
    + (rows.length > limit ? `<button class="link" id="drawer-more">Show ${int(Math.min(PAGE, rows.length - limit))} more of ${int(rows.length - limit)}</button>` : "");
  const more = $("drawer-more");
  if (more) more.addEventListener("click", () => renderDrawerBody(limit + PAGE));
  body.querySelectorAll("article.ret").forEach(wireCard);
}

function wireCard(card) {
  const id = card.dataset.id;
  const row = drawer.rows.find((r) => r.return_id === id);
  card.querySelectorAll(".verdict button").forEach((b) => b.addEventListener("click", () =>
    submitMark(card, row, { is_correct: b.dataset.v === "yes" })));
  const sel = card.querySelector("select.fix");
  if (sel) sel.addEventListener("change", () =>
    submitMark(card, row, { is_correct: false, corrected_issue: sel.value || null }));
}

async function submitMark(card, row, { is_correct, corrected_issue = null }) {
  const status = card.querySelector(".saved");
  status.className = "saved";
  status.textContent = "Saving…";
  const payload = { return_id: row.return_id, model_issue_type: row.issue_type, is_correct, corrected_issue };
  try {
    const res = await saveCorrection(payload);
    state.corrections[row.return_id] = payload;
    if (res.accuracy) state.accuracy = res.accuracy;
    const fresh = document.createElement("div");
    fresh.innerHTML = returnCard(row);
    const next = fresh.firstElementChild;
    card.replaceWith(next);
    wireCard(next);
    const s = next.querySelector(".saved");
    s.textContent = "Saved";
    refreshAccuracy();
  } catch (e) {
    status.className = "saved err";
    status.textContent = `Not saved: ${e.message}`;
  }
}

function drawerFootText() {
  const a = state.accuracy;
  const storage = state.summary && state.summary.meta.corrections_storage;
  const where = DATA_MODE !== "api" ? "saved in this browser only"
    : storage === "temporary" ? "saved until the app restarts"
    : "saved to the corrections table";
  return a.reviewed
    ? `Your checks: <strong>${int(a.correct)} of ${int(a.reviewed)}</strong> labels right (${pct(a.correct / a.reviewed)}) · ${where}`
    : `For each comment, say whether the label is right. Your answers become the accuracy number · ${where}`;
}

// ---------- boot ----------
function renderAll() {
  renderFixFirst();
  renderScale();
  renderTiles();
  renderDrivers();
  renderHotspots();
  renderLocations();
  renderTrend();
}

async function boot() {
  $("drawer-close").addEventListener("click", closeDrawer);
  $("scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden) closeDrawer(); });
  setupTrendSelect();

  try {
    const c = await getCorrections();
    state.corrections = c.corrections || {};
    state.accuracy = c.accuracy || state.accuracy;
  } catch (e) {
    console.warn("Corrections didn't load", e);
    state.correctionsError = e.message;
  }

  try {
    state.summary = await getSummary();
  } catch (e) {
    $("window-label").textContent = "";
    $("page-error").hidden = false;
    const hint = DATA_MODE === "api"
      ? `Live mode expects the API at the same address as this page. To use the sample files instead, <a href="?mode=sample">open sample mode</a>.`
      : `The sample files are missing. Run <code>python3 scripts/build_sample_data.py</code> and reload.`;
    $("page-error").innerHTML = errorBox("The dashboard couldn't load its data.", e) + `<p>${hint}</p>`;
    $("content").hidden = true;
    $("footer").textContent = "";
    return;
  }

  const m = state.summary.meta;
  $("window-label").textContent = `${m.window.label} · ${int(state.summary.headline.returns)} returns in the ${m.synthetic ? "test data" : "data"}`;
  if (m.data_mode === "sample") {
    $("banner-title").textContent = "Sample data, not real results.";
    $("sample-note").textContent = `${m.classifier_note || ""} All orders and comments are synthetic.`;
    $("sample-banner").hidden = false;
  } else if (m.synthetic) {
    $("banner-title").textContent = "Test data.";
    $("sample-note").textContent = "Labels come from the AI reading of each comment; the orders and comments themselves are synthetic.";
    $("sample-banner").hidden = false;
  }
  $("footer").textContent = `Covers ${m.window.label} · numbers worked out ${new Date(m.generated_at).toLocaleString("en-IN")} · `
    + `a problem spot needs at least ${m.min_returns_per_hotspot} returns`;

  renderFixFirst();
  renderScale();
  renderOpenQuestions();
  renderTiles();
  renderDrivers();
  renderHotspots();
  renderLocations();
  await loadTrend();

  let t;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(renderAll, 150); });
}

boot();
