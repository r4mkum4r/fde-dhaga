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


@pytest.fixture()
def stub_client(tmp_path, monkeypatch):
    """Like `client`, but on a copy of csv/ without the AI pipeline's output (the keyword-match stage)."""
    import shutil
    from app import db as dbmod, main
    d = tmp_path / "csv"
    shutil.copytree(ROOT / "csv", d, ignore=shutil.ignore_patterns("classified_returns*.csv"))
    monkeypatch.setattr(dbmod, "CSV_DIR", d)
    db = dbmod.Database(url="", sqlite_path=tmp_path / "stub.sqlite")
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
    assert body["labels_from"] == "pipeline"  # csv/classified_returns_with_dropdown.csv is committed
    assert body["corrections_storage"] == "temporary"


def test_summary_matches_sample_files(client):
    """The frontend was built on frontend/sample/. The API must return the same numbers."""
    api = client.get("/api/summary").json()
    sample = json.loads((ROOT / "frontend/sample/summary.json").read_text())
    assert strip_meta(api) == strip_meta(sample)
    assert api["meta"]["data_mode"] == sample["meta"]["data_mode"] == "live"
    assert api["meta"]["window"] == sample["meta"]["window"]
    assert api["meta"]["classifier_note"] is None


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
def test_pipeline_labels_replace_the_stand_in(stub_client):
    client = stub_client
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
    # A later pipeline run: new timestamp, so the API knows to recompute.
    conn.execute("INSERT OR REPLACE INTO classified_returns (return_id, issue_type, confidence, source, classified_at) "
                 "VALUES (?, 'fabric_quality', 0.9, 'cheap_model', '2999-01-01 00:00:00')", (rid,))
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


# ---------- the AI pipeline's CSV feeds both the static site and the API ----------
def _csv_dir_with_labels(tmp_path):
    import shutil
    d = tmp_path / "csv"
    shutil.copytree(ROOT / "csv", d, ignore=shutil.ignore_patterns("classified_returns*.csv"))
    other = [r for r in json.loads((ROOT / "frontend/sample/returns.json").read_text())["returns"]
             if r["reason_dropdown"] == "Other" and r["comment"] and r["comment"].strip(" .")]
    rid = other[0]["return_id"]
    (d / "classified_returns.csv").write_text(
        "return_id,issue_type,confidence,evidence_phrase,source,model_name\n"
        f"{rid},quality,NA,,cheap_model,test-model\n", encoding="utf-8")
    return d, rid


def test_static_build_uses_pipeline_csv(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("build", ROOT / "scripts/build_sample_data.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    csv_dir, rid = _csv_dir_with_labels(tmp_path)
    build.main(csv_dir=csv_dir, out=tmp_path / "out")
    summary = json.loads((tmp_path / "out/summary.json").read_text())
    assert summary["meta"]["data_mode"] == "live"
    assert summary["meta"]["classifier_note"] is None
    rows = {r["return_id"]: r for r in json.loads((tmp_path / "out/returns.json").read_text())["returns"]}
    assert rows[rid]["issue_type"] == "quality"
    assert rows[rid]["confidence"] is None          # "NA" in the CSV, never made up
    from app.analytics import no_usable_words
    skipped = [r for r in rows.values() if r["reason_dropdown"] == "Other" and r["return_id"] != rid]
    blank = [r for r in skipped if no_usable_words(r["comment"])]
    unread = [r for r in skipped if not no_usable_words(r["comment"])]
    # Blank comments: the code gate says so. Anything else the AI skipped: shown as failed, never guessed.
    assert blank and all(r["issue_type"] == "unclear" and r["source"] == "gate" for r in blank)
    assert unread and all(r["issue_type"] == "failed" and r["error"] == "Not read by the AI yet" for r in unread)


def test_api_loads_pipeline_csv(tmp_path, monkeypatch):
    from app import db as dbmod, main
    csv_dir, rid = _csv_dir_with_labels(tmp_path)
    monkeypatch.setattr(dbmod, "CSV_DIR", csv_dir)
    monkeypatch.setattr(main, "db", dbmod.Database(url="", sqlite_path=tmp_path / "t.sqlite"))
    main._cache.update(version=None, value=None)
    with TestClient(main.app) as c:
        assert c.get("/api/summary").json()["meta"]["data_mode"] == "live"
        rows = {r["return_id"]: r for r in c.get("/api/returns").json()["returns"]}
        assert rows[rid]["issue_type"] == "quality"


# ---------- the committed LLM output ----------
def test_raw_classifier_output_is_used_as_is(client):
    """csv/classified_returns_with_dropdown.csv: labels and the AI's reason shown; nothing made up."""
    rows = {r["return_id"]: r for r in client.get("/api/returns").json()["returns"]}
    r = rows["RT000139"]  # "SIZE M BAHUT TIGHT HAI, L LENA PADEGA ..."
    assert r["issue_type"] == "too_small" and r["source"] == "llm"
    assert r["explanation"] and r["confidence"] is None and r["evidence_phrase"] is None
    other = [x for x in rows.values() if x["reason_dropdown"] == "Other"]
    assert not [x for x in other if x["issue_type"] == "failed"]
    blank = [x for x in other if x["source"] == "gate"]
    assert blank and all(x["issue_type"] == "unclear" for x in blank)
