# Return Pulse: frontend and deployment plan (revised)

Replaces the first draft (`frontend_deploy_plan.md` at the repo root). It keeps that draft's
deployment, failure-state and pre-pitch sections, and changes the parts that didn't fit the data
we were given or the team's agreed architecture. Section 1 lists what changed and why.

**Owner:** frontend and deployment. **Depends on:** the pipeline owner, who writes `classified_returns`.

---

## 1. What changed from the first draft, and why

| First draft | Now | Why |
|---|---|---|
| Streamlit | HTML/JS page served by FastAPI | The team's architecture slides chose FastAPI plus an HTML frontend. That frontend is built and tested. One container still serves everything. |
| "This week" runs | The latest 12 months, with last year for comparison | The synthetic data has about 70 returns a week. No vendor × category × size cell reaches 20 in a week, so a weekly Fix list would be empty in the demo. |
| Hotspot = vendor × category × size | Vendor × product type, with size as a column | Grouping by size spread V21's quality and V12's colour problems too thin to show. Size is still reported when 20+ returns back it. |
| Taxonomy `fabric_quality`, `late_delivery` | `quality`, `delivery_late` | Must match `classified_returns` and `eval_return_labels`, or accuracy is silently wrong for two reasons. |
| 500 hand-written fixture rows with a planted hotspot | Sample files generated from `csv/` by code | The data already has 6,571 realistic returns. Hand-planting is the curated demo path the brief marks down. |
| "eval accuracy on comments labelled by hand" on screen | Kept out of the app. It goes in the build note as accuracy against the synthetic answer key | The labels weren't made by hand. The answer sheet isn't loaded by the app or shipped to the Space. |
| Live "Analyse this week" button | Cut | A public button that spends money, for a demo the latest saved run already covers. The pipeline runs offline and writes `classified_returns`. The page switches to its labels automatically. |
| Six pages, admin passcode, backup host | One page, an admin command line, one host | "An MVP that does one thing convincingly beats four half-built features" (brief §15). |
| Not in the draft | "What to fix first", "At Dhaga's scale", a source tag on every number, "What we still need from Dhaga" | Brief §13–14: no narration needed, and say which numbers came from the case study before the room asks. |

## 2. What's built

```
app/
  main.py          FastAPI: /api/summary, /api/trend, /api/returns, /api/corrections (GET, POST), /health; serves frontend/
  analytics.py     every rule (labels, problem spots, cities, trend). Shared with the sample builder
  db.py            Postgres when DATABASE_URL is set, else SQLite built from csv/ on first start
  admin.py         python -m app.admin clear-corrections | rebuild-sqlite
frontend/          the dashboard (see frontend/README.md); API_CONTRACT.md is the contract
scripts/build_sample_data.py   regenerates frontend/sample/ for frontend-only work
tests/test_api.py  13 tests: contract, corrections, pipeline switch-over, database down, answer sheet kept out
Dockerfile, .github/workflows/{ci,deploy,keepalive}.yml, deploy/space_header.md, .env.example
```

**The pipeline handover.** The pipeline writes rows to `classified_returns` (schema in `sql/01_schema.sql`).
When the table is empty, the API labels "Other" comments with the keyword stand-in and the page says so.
Once it has rows, the API uses them, recomputes, and the banner changes. A comment the pipeline skipped
is shown as **failed: "Not read by the AI yet"**, never guessed. No code change, no restart.

## 3. Failure states (all visible on screen)

| Condition | What the screen says | Covered by |
|---|---|---|
| Database unreachable | Red box: "Can't reach the database at host:port/db". Numbers hidden, never stale. Password never shown | `test_database_down_says_so` |
| No returns in the database | "The database has no returns yet. Load the data, then reload this page." | Code path in `main.state()` |
| Pipeline hasn't run | Yellow banner: "Other" comments are sorted by a simple keyword match until the AI reading runs | `test_summary_matches_sample_files` |
| Pipeline skipped a comment | Card marked failed: "Not read by the AI yet" | `test_pipeline_labels_replace_the_stand_in` |
| Comment unclear / failed | Own tile, own bars, own list; cards show the reason | `test_returns_filters_line_up_with_summary` |
| Evidence phrase not in the comment | Comment shown without a highlight | `test_pipeline_labels_replace_the_stand_in`, `test_evidence_is_always_inside_the_comment` |
| Correction can't save | Card says "Not saved: {reason}" | Browser check; API refusals in `test_bad_corrections_are_refused` |
| Corrections on SQLite (Space without Postgres) | Drawer: "saved until the app restarts" | Browser check |
| Trend or comments fail alone | That panel shows the error; the rest of the page stays | Browser check |

Customer comments are untrusted: the frontend escapes all text before rendering.

## 4. Deployment: a static Hugging Face Space (free, no server)

The hosted dashboard is a **static site**: the page plus JSON built from `csv/` by
`scripts/build_sample_data.py`. No server runs, so nothing sleeps or costs money. If
`csv/classified_returns.csv` (the AI pipeline's output) is committed, the page shows those labels.

| | Static Space (hosted) | FastAPI (local, or a container host) |
|---|---|---|
| ✓/✗ marks | Kept in each viewer's own browser | Kept in the `corrections` table, shared |
| API for other teams | Not on the hosted URL | Yes, at `/api/...` |
| New data | Commit the CSV; the deploy rebuilds | Picked up when the pipeline writes |

**One-time setup**
1. Create the Space at huggingface.co/new-space: SDK **Static**, **Public** (the brief wants anyone with the
   link to open it). Prefer a group organisation over one person's account.
2. Create a Hugging Face token with **Write** access.
3. In the GitHub repo, under Settings → Secrets and variables → Actions:
   - secret `HF_TOKEN`: the token
   - variable `HF_SPACE`: `<owner>/<space>`
   - variable `HF_SPACE_URL`: the address the Space page shows for the running site
4. Push to `main`. `deploy.yml` runs the tests, builds the data from the CSVs, publishes only the page and
   its data (no server code, no answer sheet, no binary files), and waits until the live page shows the
   new commit. A broken deploy turns the GitHub job red.

**Rollback:** tag the last good commit `demo-ready`, then run the deploy workflow manually with `ref: demo-ready`.

## 5. Local cold start (target: under five minutes)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Open http://localhost:8000. No keys and no database are needed: SQLite is built from `csv/` on first start.
Docker: `docker build -t return-pulse . && docker run -p 7860:7860 return-pulse`, then open http://localhost:7860.
Tests: `pip install -r requirements-dev.txt && pytest -q`.

**Test it:** a teammate who hasn't touched this follows the steps above with a timer. Fix whatever slows them down.

## 6. Remaining work

| # | Task | Owner | Done when |
|---|---|---|---|
| 1 | First deploy (section 4) | Frontend/deploy | `/health` on the Space shows the commit SHA, and a second merge changes it without anyone touching the Space |
| 2 | Shared corrections (optional) | Frontend/deploy | Only if Neha's marks must be shared: host the FastAPI app with Postgres instead of the static site |
| 3 | Pipeline writes `classified_returns` | Pipeline | The banner changes from "keyword match" to "Test data", and the unclear and failed counts are the pipeline's |
| 4 | Accuracy and cost lines in the build note | Pipeline + frontend | Accuracy against `eval_return_labels`, and cost per run at Dhaga's 48,000 orders a week, with the arithmetic shown |
| 5 | Agree the open questions with the client | Pitch owner | The "What we still need from Dhaga" list is answered, or presented as the ask |
| 6 | Timed cold start and a never-seen device | Anyone but the builder | Under five minutes; the live URL works on a phone nobody in the group has used |
| 7 | Tag `demo-ready`; record a 90-second fallback video | Frontend/deploy | Both exist the day before |

## 7. Pre-pitch checklist

**The day before**
- [ ] `main` frozen, tagged `demo-ready`, CI green
- [ ] Live URL shared with the mentors so they can open it cold
- [ ] Postgres free tier not paused (some providers pause idle databases)

**60 minutes before**
- [ ] Open the live URL on a phone and a laptop
- [ ] On the presenting laptop, open the live URL in a fresh private window, so the demo's ✓/✗ marks are the first ones
- [ ] Screen recording ready on the presenting laptop

**Rollback triggers, decided now**
- The live URL doesn't load 30 minutes before: redeploy `demo-ready`. If it still fails, run locally and say so.
- Both fail: play the recording, and say plainly that the live deploy is down.

## 8. The demo path (from the discovery note)

Neha opens the URL → **What to fix first** says V07's kurtis and dresses run small in L and M → she clicks
**Read the comments** → reads "SIZE M BAHUT TIGHT HAI, L LENA PADEGA" → marks a wrong label ✗ and picks the right
one → accuracy updates → **Couldn't classify → Read them** shows the unclear comments honestly. That last step
is the failure case the brief asks you to show on purpose.
