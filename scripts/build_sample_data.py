"""
Build the frontend's SAMPLE data: JSON files in the exact shape the API returns.

Use these to work on the frontend without running the API:
    python3 -m http.server 5173 --directory frontend   then open  http://localhost:5173/?mode=sample

The rules live in app/analytics.py, shared with the API, so sample files and API agree.
"Other" comments are labelled by the keyword stand-in there (not a model), and every row it
labels says so. The answer sheet (eval_return_labels) is NOT read here.

Run:   python3 scripts/build_sample_data.py
Needs: Python 3.10+, no packages. Reads csv/, writes frontend/sample/.
"""
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import analytics  # noqa: E402

CSV = ROOT / "csv"
OUT = ROOT / "frontend" / "sample"


def read(name):
    with open(CSV / f"{name}.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def enriched_from_csv():
    """The returns_enriched view, rebuilt from the CSVs."""
    vendors = {v["vendor_id"]: v for v in read("vendors")}
    products = {p["sku"]: p for p in read("products")}
    orders = {o["order_id"]: o for o in read("orders")}
    items = {i["order_item_id"]: i for i in read("order_items")}
    rows = []
    for r in read("returns"):
        o, i, p = orders[r["order_id"]], items[r["order_item_id"]], products[r["sku"]]
        rows.append({**r, "ship_city": o["ship_city"], "ship_state": o["ship_state"], "size": i["size"],
                     "product_name": p["product_name"], "department": p["department"],
                     "subcategory": p["subcategory"], "vendor_id": p["vendor_id"],
                     "vendor_name": vendors[p["vendor_id"]]["vendor_name"]})
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    settings = analytics.DEFAULT_SETTINGS
    rows, _ = analytics.label_rows(enriched_from_csv(), None, settings)
    wins = analytics.windows(max(r["return_date"] for r in rows))
    current = [r for r in rows if analytics.in_window(r, wins["this"])]

    meta = {
        "data_mode": "sample",
        "synthetic": True,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "window": {"from": wins["this"][0].isoformat(), "to": wins["this"][1].isoformat(), "label": wins["this"][2]},
        "confidence_threshold": settings["confidence_threshold"],
        "min_returns_per_hotspot": settings["min_returns_per_hotspot"],
        "classifier_note": "The AI reading hasn't run yet, so 'Other' comments are sorted by a simple keyword match. Expect more mistakes than the final version.",
    }
    summary = {"meta": meta, **analytics.summarize(current, wins["this"], settings)}
    trend = {k: analytics.trend(rows, wins, k) for k in analytics.ISSUES}

    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "trend.json").write_text(json.dumps(trend, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "returns.json").write_text(json.dumps({"meta": meta, "returns": current}, ensure_ascii=False,
                                                 separators=(",", ":")), encoding="utf-8")
    h = summary["headline"]
    print(f"{h['returns']} returns in {wins['this'][2]}; known reason {h['known_reason_before']:.1%} -> "
          f"{h['known_reason_after']:.1%}; unclear {h['unclear']}, failed {h['failed']}; "
          f"{len(summary['hotspots'])} problem spots, {len(summary['locations'])} cities. Wrote {OUT}")


if __name__ == "__main__":
    main()
