// Every call the dashboard makes to get data goes through this file.
// "api": the FastAPI app (app/main.py) serves this page and the data.
// "sample": the pre-built JSON files in frontend/sample/, made from the CSVs. The hosted static
//           site uses this; the deploy sets it in index.html's <meta name="return-pulse-data">.
// ?mode=api or ?mode=sample in the address overrides it.
// The response shapes are in frontend/API_CONTRACT.md. Sample files follow them exactly.

const DATA_MODE_DEFAULT =
  document.querySelector('meta[name="return-pulse-data"]')?.content || "api"; // "api" | "sample"
const API_BASE = ""; // same origin as FastAPI; e.g. "http://localhost:8000" when served separately

export const DATA_MODE = new URLSearchParams(location.search).get("mode") || DATA_MODE_DEFAULT;

export class ApiError extends Error {
  constructor(message, detail) {
    super(message);
    this.detail = detail;
  }
}

async function getJson(url) {
  let res;
  try {
    res = await fetch(url, { headers: { Accept: "application/json" } });
  } catch (e) {
    throw new ApiError(`Couldn't reach ${url}`, String(e));
  }
  if (!res.ok) {
    let body = "";
    try { body = await res.text(); } catch (_) {}
    // Servers often answer errors with an HTML page; keep only its readable text.
    const text = body.replace(/<style[\s\S]*?<\/style>/gi, "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
    throw new ApiError(`${url} answered ${res.status} ${res.statusText}`.trim(), text.slice(0, 200));
  }
  try {
    return await res.json();
  } catch (e) {
    throw new ApiError(`${url} didn't return valid JSON`, String(e));
  }
}

// ---------- sample mode helpers ----------
let sampleReturns = null;
async function sampleRows() {
  if (!sampleReturns) sampleReturns = (await getJson("sample/returns.json")).returns;
  return sampleReturns;
}

const CORRECTIONS_KEY = "dhaga.sample.corrections";
function readSampleCorrections() {
  try { return JSON.parse(localStorage.getItem(CORRECTIONS_KEY) || "{}"); } catch (_) { return {}; }
}
function writeSampleCorrections(all) {
  try { localStorage.setItem(CORRECTIONS_KEY, JSON.stringify(all)); } catch (_) {}
}
function accuracyFrom(all) {
  const marks = Object.values(all);
  return { reviewed: marks.length, correct: marks.filter((m) => m.is_correct).length };
}

// ---------- public calls ----------

/** GET /api/summary → { meta, headline, drivers, hotspots, locations } */
export async function getSummary() {
  if (DATA_MODE === "api") return getJson(`${API_BASE}/api/summary`);
  return getJson("sample/summary.json");
}

/** GET /api/trend?issue=too_small → { issue, label, months, series: [{key,label,counts}] } */
export async function getTrend(issue) {
  if (DATA_MODE === "api") return getJson(`${API_BASE}/api/trend?issue=${encodeURIComponent(issue)}`);
  const all = await getJson("sample/trend.json");
  if (!all[issue]) throw new ApiError(`No trend for "${issue}"`);
  return all[issue];
}

/**
 * GET /api/returns?vendor_id=&subcategory=&city=&issue_type=
 * → { returns: [ ...rows ], total }
 * Any filter may be omitted. issue_type accepts "unclear" and "failed".
 */
export async function getReturns(filters) {
  if (DATA_MODE === "api") {
    const q = new URLSearchParams(Object.entries(filters).filter(([, v]) => v != null && v !== ""));
    return getJson(`${API_BASE}/api/returns?${q}`);
  }
  const rows = (await sampleRows()).filter((r) =>
    Object.entries(filters).every(([k, v]) => v == null || v === "" || r[k] === v));
  return { returns: rows, total: rows.length };
}

/** GET /api/corrections → { corrections: { [return_id]: {is_correct, ...} }, accuracy: {reviewed, correct} } */
export async function getCorrections() {
  if (DATA_MODE === "api") return getJson(`${API_BASE}/api/corrections`);
  const all = readSampleCorrections();
  return { corrections: all, accuracy: accuracyFrom(all) };
}

/**
 * POST /api/corrections { return_id, model_issue_type, is_correct }
 * → { ok: true, accuracy: {reviewed, correct} }
 * Sending the same return_id again replaces Neha's earlier mark.
 */
export async function saveCorrection(c) {
  if (DATA_MODE === "api") {
    let res;
    try {
      res = await fetch(`${API_BASE}/api/corrections`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(c),
      });
    } catch (e) {
      throw new ApiError("Couldn't save the mark: the server didn't answer", String(e));
    }
    if (!res.ok) throw new ApiError(`Couldn't save the mark (${res.status})`, await res.text());
    return res.json();
  }
  const all = readSampleCorrections();
  all[c.return_id] = { ...c, corrected_at: new Date().toISOString() };
  writeSampleCorrections(all);
  return { ok: true, accuracy: accuracyFrom(all) };
}
