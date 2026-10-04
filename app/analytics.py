"""
The dashboard's rules, in one place: how returns get a label, and how problem spots,
cities and the trend are worked out. The API (app/main.py) and the sample-file
builder (scripts/build_sample_data.py) both call these, so they can never disagree.

Everything here is plain code: counting, comparing, ranking. No model is called.
The only "reading" of text is stub_classify, a keyword stand-in that is used ONLY
until the real pipeline has written rows to classified_returns.
"""
import hashlib
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

# Issue types: same values as classified_returns.issue_type and eval_return_labels.true_issue.
ISSUES = {
    "too_small": "Too small",
    "too_large": "Too large",
    "colour_mismatch": "Colour mismatch",
    "quality": "Quality",
    "damaged": "Damaged",
    "wrong_item": "Wrong item",
    "changed_mind": "Changed mind",
    "delivery_late": "Delivery late",
    "unclear": "Unclear",
    "failed": "Failed",
}
NOT_CLASSIFIED = ("unclear", "failed")

DROPDOWN_TO_ISSUE = {
    "Size too small": "too_small",
    "Size too large": "too_large",
    "Colour different from image": "colour_mismatch",
    "Quality not as expected": "quality",
    "Product damaged": "damaged",
    "Wrong item received": "wrong_item",
}

# Defaults match the `settings` table in sql/01_schema.sql; the API reads the table.
DEFAULT_SETTINGS = {"confidence_threshold": 0.70, "min_returns_per_hotspot": 20}
MIN_RETURNS_PER_CITY = 40
MIN_LIFT = 0.05  # a group must sit at least 5 points above the overall share to be listed

# ---------------------------------------------------------------------------
# Keyword stand-in for the model. Crude on purpose: it exists so the screens have
# real-shaped right, wrong, unclear and failed labels before the pipeline runs.
# ---------------------------------------------------------------------------
KEYWORDS = {
    "too_small": ["chhota", "chota", "chhoti", "tight", "small", "short"],
    "too_large": ["loose", "bada", "badi", "large", "zyada", "lamba", "dheela", "oversized"],
    "colour_mismatch": ["colour", "color", "mangaya", "dikha", "dull", "bright", "shade", "photo me", "image me", "rang"],
    "quality": ["stitching", "dhaage", "patla", "thin", "see through", "shrink", "dhulai", "fabric", "kapda", "silai", "material", "print utar", "wash"],
    "damaged": ["phata", "toota", "tuta", "damage", "daag", "stain", "zip", "button", "ganda"],
    "wrong_item": ["galat", "wrong", "mera order", "dusra"],
    "changed_mind": ["mind change", "zarurat", "function cancel", "pasand nahi", "gift", "accidentally", "nahi chahiye bas", "sasta", "anymore"],
    "delivery_late": ["late", "der se", "din lag", "baad aaya", "time pe nahi"],
}
STUB_MODEL_NAME = "sample-keyword-stub"


def stable_fraction(key):
    """Deterministic 0..1 value from an id, so every run gives the same output."""
    return int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def stub_classify(return_id, text, confidence_threshold=DEFAULT_SETTINGS["confidence_threshold"]):
    """Returns (issue_type, confidence, evidence_phrase, source, error)."""
    clean = (text or "").strip()
    letters = re.sub(r"[^A-Za-zऀ-ॿ]", "", clean)
    if len(letters) < 3:
        return "unclear", None, None, "gate", "No usable words in the comment"
    if stable_fraction("fail" + return_id) < 0.015:
        return "failed", None, None, "sample_stub", "Simulated failure: the AI's answer was unreadable twice"

    low = clean.lower()
    hits = []
    for issue, words in KEYWORDS.items():
        for w in words:
            pos = low.find(w)
            if pos >= 0:
                hits.append((pos, issue, w))
                break
    if not hits:
        return "unclear", round(0.3 + 0.2 * stable_fraction(return_id), 2), None, "sample_stub", None
    hits.sort()
    _, issue, word = hits[0]
    conf = 0.92 if len({h[1] for h in hits}) == 1 else 0.62
    conf = round(conf - 0.08 * stable_fraction("c" + return_id), 2)
    source = "sample_stub_low_conf" if conf < confidence_threshold else "sample_stub"
    return issue, conf, word, source, None


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------
def label_rows(enriched, classified=None, settings=DEFAULT_SETTINGS):
    """
    enriched:   rows from the returns_enriched view (dicts).
    classified: {return_id: classified_returns row} from the pipeline, or None.
                None means the pipeline hasn't run: "Other" comments go to the stub.
    Returns (rows, label_source) where label_source is "pipeline" or "sample_stub".
    """
    use_stub = not classified
    out = []
    for r in enriched:
        rid = r["return_id"]
        dropdown = r["reason_dropdown"]
        if dropdown != "Other":
            issue, conf, evidence, source, model, error = DROPDOWN_TO_ISSUE.get(dropdown, "unclear"), 1.0, None, "dropdown", None, None
        elif use_stub:
            issue, conf, evidence, source, error = stub_classify(rid, r["other_text"], settings["confidence_threshold"])
            model = None if source == "gate" else STUB_MODEL_NAME
        elif rid in classified:
            c = classified[rid]
            issue = c["issue_type"]
            if issue not in ISSUES:
                c = {**c, "evidence_phrase": None}
                issue = "failed"
            conf = float(c["confidence"]) if c.get("confidence") is not None else None
            evidence, source, model = c.get("evidence_phrase"), c["source"], c.get("model_name")
            if c["issue_type"] not in ISSUES:
                error = f"The AI returned an unknown issue type '{c['issue_type']}'"
            else:
                error = "The AI couldn't read this comment" if issue == "failed" else None
        else:
            # Shown on screen as failed, never guessed: the pipeline skipped this one.
            issue, conf, evidence, source, model, error = "failed", None, None, "pipeline", None, "Not read by the AI yet"
        # Highlight only if the phrase really is in the comment.
        if evidence and r["other_text"] and evidence.lower() not in r["other_text"].lower():
            evidence = None
        out.append({
            "return_id": rid,
            "return_date": _iso(r["return_date"]),
            "sku": r["sku"],
            "product_name": r["product_name"],
            "subcategory": r["subcategory"],
            "department": r["department"],
            "vendor_id": r["vendor_id"],
            "vendor_name": r["vendor_name"],
            "size": r["size"],
            "city": r["ship_city"],
            "state": r["ship_state"],
            "reason_dropdown": dropdown,
            "comment": r["other_text"] or None,
            "issue_type": issue,
            "confidence": conf,
            "evidence_phrase": evidence,
            "source": source,
            "model_name": model,
            "error": error,
        })
    return out, ("sample_stub" if use_stub else "pipeline")


def _iso(d):
    return d if isinstance(d, str) else d.isoformat()


# ---------------------------------------------------------------------------
# Windows: the latest twelve whole months in the data, and the twelve before.
# ---------------------------------------------------------------------------
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def windows(latest):
    """Given the latest return date, the current and previous twelve-month windows."""
    latest = date.fromisoformat(latest) if isinstance(latest, str) else latest
    end_month = date(latest.year, latest.month, 1)
    this_start = _add_months(end_month, -11)
    this_end = _add_months(end_month, 1) - timedelta(days=1)
    last_start = _add_months(this_start, -12)
    last_end = this_start - timedelta(days=1)
    fmt = lambda d: f"{MONTH_NAMES[d.month - 1]} {d.year}"
    return {
        "this": (this_start, this_end, f"{fmt(this_start)} – {fmt(this_end)}"),
        "last": (last_start, last_end, f"{fmt(last_start)} – {fmt(last_end)}"),
        "months": [MONTH_NAMES[_add_months(this_start, i).month - 1] for i in range(12)],
    }


def in_window(row, w):
    d = date.fromisoformat(row["return_date"])
    return w[0] <= d <= w[1]


# ---------------------------------------------------------------------------
# Summary: headline, drivers, problem spots, locations
# ---------------------------------------------------------------------------
def summarize(rows, w, settings=DEFAULT_SETTINGS):
    """rows: labelled rows already limited to the current window."""
    n = len(rows)
    if n == 0:
        return {"headline": {"returns": 0, "known_reason_before": 0, "known_reason_after": 0,
                             "other_comments": 0, "unclear": 0, "failed": 0},
                "drivers": [{"issue": k, "label": v, "count": 0, "share": 0} for k, v in ISSUES.items()],
                "hotspots": [], "locations": []}
    min_hotspot = int(settings["min_returns_per_hotspot"])
    issue_counts = Counter(r["issue_type"] for r in rows)
    known_before = sum(1 for r in rows if r["reason_dropdown"] != "Other")
    known_after = n - issue_counts["unclear"] - issue_counts["failed"]
    overall = {k: issue_counts[k] / n for k in ISSUES}

    # Problem spots: vendor x product type. Size is reported, not grouped on, so problems that
    # aren't about fit (colour, quality) still reach the minimum count.
    groups = defaultdict(list)
    for r in rows:
        groups[(r["vendor_id"], r["subcategory"])].append(r)
    hotspots = []
    for (vid, sub), rs in groups.items():
        if len(rs) < min_hotspot:
            continue
        c = Counter(r["issue_type"] for r in rs if r["issue_type"] not in NOT_CLASSIFIED)
        if not c:
            continue
        top = max(c, key=lambda k: (c[k] / len(rs)) - overall[k])
        top_n = c[top]
        share = top_n / len(rs)
        if share - overall[top] < MIN_LIFT:
            continue
        sizes = Counter(r["size"] for r in rs if r["issue_type"] == top)
        size_label = "All sizes"
        if top_n >= min_hotspot:
            top_sizes = sizes.most_common(2)
            if top_sizes[0][1] / top_n >= 0.5:
                size_label = top_sizes[0][0]
            elif sum(cnt for _, cnt in top_sizes) / top_n >= 0.5:
                size_label = ", ".join(sz for sz, _ in top_sizes)
        hotspots.append({
            "id": f"{vid}|{sub}",
            "vendor_id": vid,
            "vendor_name": rs[0]["vendor_name"],
            "subcategory": sub,
            "size": size_label,
            "size_breakdown": dict(sizes.most_common()),
            "returns": len(rs),
            "top_issue": top,
            "top_issue_label": ISSUES[top],
            "top_issue_count": top_n,
            "top_issue_share": round(share, 4),
            "baseline_share": round(overall[top], 4),
            "unclear_or_failed": sum(1 for r in rs if r["issue_type"] in NOT_CLASSIFIED),
        })
    hotspots.sort(key=lambda h: (h["top_issue_share"] - h["baseline_share"]) * h["returns"], reverse=True)

    by_city = defaultdict(list)
    for r in rows:
        by_city[r["city"]].append(r)
    locations = []
    for city, rs in by_city.items():
        if len(rs) < MIN_RETURNS_PER_CITY:
            continue
        c = Counter(r["issue_type"] for r in rs if r["issue_type"] not in NOT_CLASSIFIED)
        if not c:
            continue
        best = max(c, key=lambda k: (c[k] / len(rs)) - overall[k])
        if c[best] / len(rs) - overall[best] < MIN_LIFT:
            continue
        locations.append({
            "city": city,
            "state": rs[0]["state"],
            "returns": len(rs),
            "top_issue": best,
            "top_issue_label": ISSUES[best],
            "top_issue_count": c[best],
            "top_issue_share": round(c[best] / len(rs), 4),
            "baseline_share": round(overall[best], 4),
        })
    locations.sort(key=lambda l: (l["top_issue_share"] - l["baseline_share"]) * l["returns"], reverse=True)

    return {
        "headline": {
            "returns": n,
            "known_reason_before": round(known_before / n, 4),
            "known_reason_after": round(known_after / n, 4),
            "other_comments": n - known_before,
            "unclear": issue_counts["unclear"],
            "failed": issue_counts["failed"],
        },
        "drivers": [{"issue": k, "label": v, "count": issue_counts[k], "share": round(issue_counts[k] / n, 4)}
                    for k, v in ISSUES.items()],
        "hotspots": hotspots,
        "locations": locations,
    }


def trend(all_rows, wins, issue):
    """Monthly counts of one issue: previous twelve months against the latest twelve."""
    this_w, last_w = wins["this"], wins["last"]
    series = {"this_year": [0] * 12, "last_year": [0] * 12}
    for r in all_rows:
        if r["issue_type"] != issue:
            continue
        d = date.fromisoformat(r["return_date"])
        for key, w in (("this_year", this_w), ("last_year", last_w)):
            if w[0] <= d <= w[1]:
                idx = (d.year - w[0].year) * 12 + d.month - w[0].month
                series[key][idx] += 1
    return {
        "issue": issue,
        "label": ISSUES[issue],
        "months": wins["months"],
        "series": [
            {"key": "last_year", "label": last_w[2], "counts": series["last_year"]},
            {"key": "this_year", "label": this_w[2], "counts": series["this_year"]},
        ],
    }


CLASSIFIED_COLUMNS = ("return_id", "issue_type", "confidence", "evidence_phrase", "source", "model_name")


def read_classified_csv(path):
    """
    The pipeline's output (csv/classified_returns.csv) as {return_id: row}, or None if the file
    doesn't exist. Blank, NA and nan become None. A missing column is an error with a plain message.
    """
    import csv
    from pathlib import Path
    path = Path(path)
    if not path.exists():
        return None
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in ("return_id", "issue_type", "source") if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path.name} is missing columns: {', '.join(missing)}")
        out = {}
        for row in reader:
            clean = {}
            for c in CLASSIFIED_COLUMNS:
                v = (row.get(c) or "").strip()
                clean[c] = None if v.lower() in ("", "na", "nan", "none", "null") else v
            out[clean["return_id"]] = clean
    return out


RETURN_FILTERS = ("vendor_id", "subcategory", "city", "issue_type")


def filter_returns(rows, filters):
    active = {k: v for k, v in filters.items() if k in RETURN_FILTERS and v not in (None, "")}
    return [r for r in rows if all(r[k] == v for k, v in active.items())]
