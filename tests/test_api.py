import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A fresh app on a fresh SQLite copy, built from csv/."""
    from app import main
    from app.db import Database

    db = Database(url="", sqlite_path=tmp_path / "test.sqlite")
    monkeypatch.setattr(main, "db", db)
    main._cache.update(version=None, value=None)
    with TestClient(main.app) as c:
        c.db = db
        yield c


def strip_meta(d):
    return {k: v for k, v in d.items() if k != "meta"}


# ---------- contract ----------
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["tables"]["returns"] == 6571
    assert body["labels_from"] == "sample_stub"
    assert body["corrections_storage"] == "temporary"


def test_summary_matches_sample_files(client):
    """The frontend was built on frontend/sample/. The API must return the same numbers."""
    api = client.get("/api/summary").json()
    sample = json.loads((ROOT / "frontend/sample/summary.json").read_text())
    assert strip_meta(api) == strip_meta(sample)
    assert api["meta"]["data_mode"] == "sample"
    assert api["meta"]["window"] == sample["meta"]["window"]
    assert api["meta"]["classifier_note"]


def test_trend(client):
    sample = json.loads((ROOT / "frontend/sample/trend.json").read_text())
    for issue in ["too_small", "unclear", "failed"]:
        t = client.get("/api/trend", params={"issue": issue}).json()
        assert t == sample[issue]
        assert [s["key"] for s in t["series"]] == ["last_year", "this_year"]
        assert all(len(s["counts"]) == 12 for s in t["series"])
    assert client.get("/api/trend", params={"issue": "fabric_quality"}).status_code == 400


def test_returns_filters_line_up_with_summary(client):
    s = client.get("/api/summary").json()
    spot = s["hotspots"][0]
    r = client.get("/api/returns", params={"vendor_id": spot["vendor_id"], "subcategory": spot["subcategory"]}).json()
    assert r["total"] == spot["returns"] == len(r["returns"])
    unclear = client.get("/api/returns", params={"issue_type": "unclear"}).json()
    assert unclear["total"] == s["headline"]["unclear"]
    failed = client.get("/api/returns", params={"issue_type": "failed"}).json()
    assert failed["total"] == s["headline"]["failed"]
    assert all(row["error"] for row in failed["returns"])


def test_top_spots_are_found_by_the_rules(client):
    """The generated data hides these patterns (data README); the rules must find them unaided."""
    spots = client.get("/api/summary").json()["hotspots"][:6]
    found = {(h["vendor_id"], h["top_issue"]) for h in spots}
    assert {("V07", "too_small"), ("V12", "colour_mismatch"), ("V21", "quality")} <= found


def test_evidence_is_always_inside_the_comment(client):
    rows = client.get("/api/returns").json()["returns"]
    for r in rows:
        if r["evidence_phrase"]:
            assert r["evidence_phrase"].lower() in r["comment"].lower()


# ---------- corrections ----------
def test_corrections_round_trip(client):
    assert client.get("/api/corrections").json()["accuracy"] == {"reviewed": 0, "correct": 0}
    ok = client.post("/api/corrections", json={"return_id": "RT000139", "model_issue_type": "too_small", "is_correct": True})
    assert ok.status_code == 200 and ok.json()["accuracy"] == {"reviewed": 1, "correct": 1}
    wrong = client.post("/api/corrections", json={"return_id": "RT000556", "model_issue_type": "too_small",
                                                  "is_correct": False, "corrected_issue": "too_large"})
    assert wrong.json()["accuracy"] == {"reviewed": 2, "correct": 1}
    # Neha changes her mind: the latest mark wins.
    client.post("/api/corrections", json={"return_id": "RT000556", "model_issue_type": "too_small", "is_correct": True})
    got = client.get("/api/corrections").json()
    assert got["accuracy"] == {"reviewed": 2, "correct": 2}
    assert got["corrections"]["RT000556"]["is_correct"] is True


@pytest.mark.parametrize("payload,status", [
    ({"return_id": "RT999999", "model_issue_type": "too_small", "is_correct": True}, 404),
    ({"return_id": "RT000139", "model_issue_type": "fabric_quality", "is_correct": True}, 422),
    ({"return_id": "RT000139", "model_issue_type": "too_small", "is_correct": True, "corrected_issue": "quality"}, 400),
])
def test_bad_corrections_are_refused(client, payload, status):
    assert client.post("/api/corrections", json=payload).status_code == status


# ---------- the switch from stand-in to pipeline labels ----------
def test_pipeline_labels_replace_the_stand_in(client):
    assert client.get("/api/summary").json()["meta"]["data_mode"] == "sample"  # also builds the SQLite copy
    conn = sqlite3.connect(client.db.sqlite_path)
    other = [r[0] for r in conn.execute(
        "SELECT return_id FROM returns WHERE reason_dropdown = 'Other' AND return_date >= '2025-10-01' ORDER BY return_id")]
    labelled, skipped = other[0], other[1]
    conn.execute("INSERT INTO classified_returns (return_id, issue_type, confidence, evidence_phrase, source, model_name) "
                 "VALUES (?, 'quality', 0.91, 'not in the comment at all', 'cheap_model', 'test-model')", (labelled,))
    conn.commit()
    conn.close()

    s = client.get("/api/summary").json()
    assert s["meta"]["data_mode"] == "live"
    assert s["meta"]["classifier_note"] is None
    rows = {r["return_id"]: r for r in client.get("/api/returns").json()["returns"]}
    assert rows[labelled]["issue_type"] == "quality"
    assert rows[labelled]["source"] == "cheap_model"
    assert rows[labelled]["evidence_phrase"] is None  # not a substring, so not highlighted
    # A comment the pipeline didn't label is shown as failed with a reason, never guessed.
    assert rows[skipped]["issue_type"] == "failed"
    assert rows[skipped]["error"] == "Not read by the AI yet"


# ---------- failing visibly ----------
def test_database_down_says_so(monkeypatch):
    from app import main
    from app.db import Database

    monkeypatch.setattr(main, "db", Database(url="postgresql://neha:s3cret@127.0.0.1:1/dhaga"))
    main._cache.update(version=None, value=None)
    with TestClient(main.app) as c:
        for path in ["/api/summary", "/api/returns", "/health"]:
            r = c.get(path)
            assert r.status_code == 503, path
            text = r.text
            assert "s3cret" not in text
        assert "Can't reach the database" in c.get("/api/summary").json()["detail"]
        # The page itself still loads, so it can show the error.
        assert c.get("/").status_code == 200


def test_answer_sheet_is_never_loaded(client):
    client.get("/health")
    conn = sqlite3.connect(client.db.sqlite_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "eval_return_labels" not in tables


def test_unknown_pipeline_issue_shows_as_failed(client):
    client.get("/health")
    conn = sqlite3.connect(client.db.sqlite_path)
    rid = conn.execute("SELECT return_id FROM returns WHERE reason_dropdown = 'Other' AND return_date >= '2025-10-01' "
                       "ORDER BY return_id LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO classified_returns (return_id, issue_type, confidence, source) "
                 "VALUES (?, 'fabric_quality', 0.9, 'cheap_model')", (rid,))
    conn.commit()
    conn.close()
    r = client.get("/api/returns", params={"issue_type": "failed"})
    assert r.status_code == 200
    row = next(x for x in r.json()["returns"] if x["return_id"] == rid)
    assert "unknown issue type 'fabric_quality'" in row["error"]


def test_published_schema_covers_every_endpoint(client):
    spec = client.get("/api/openapi.json").json()
    assert set(spec["paths"]) == {"/health", "/api/summary", "/api/trend", "/api/returns", "/api/corrections"}
    assert set(spec["paths"]["/api/corrections"]) == {"get", "post"}
    for path, ops in spec["paths"].items():
        for op in ops.values():
            assert "$ref" in str(op["responses"]["200"]), f"{path} has no response schema"


def test_browsers_always_check_for_a_new_version(client):
    for path in ["/", "/js/app.js", "/css/styles.css", "/api/summary"]:
        assert client.get(path).headers["cache-control"] == "no-cache", path
