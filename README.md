# Return Pulse: why Dhaga & Co. products come back

An internal dashboard for Neha (Category Head). It reads every return, including the
free-text "Other" comments, and shows what to fix first: which vendor, product type, size and city, with the
customers' own words as proof. **All data in this repo is synthetic.**

## Run it (under five minutes, no keys, no database)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Open http://localhost:8000. On first start the app builds a local SQLite copy from `csv/`. To use the team's
Postgres instead, copy `.env.example` to `.env`, set `DATABASE_URL`, and export it before starting.

- **What it expects:** the tables in `sql/01_schema.sql`. Once the pipeline writes `classified_returns`, the page
  uses those labels. Until then it uses a keyword stand-in and says so in a yellow banner.
- **When something goes wrong:** it says so on screen. Database down, unclear and failed comments, a mark that
  didn't save: see the failure table in `docs/frontend_deploy_plan.md` §3. `GET /health` reports the state.
- **Tests:** `pip install -r requirements-dev.txt && pytest -q`
- **Docker / Hugging Face Space:** `docker build -t return-pulse . && docker run -p 7860:7860 return-pulse`.
  Deployment setup is in `docs/frontend_deploy_plan.md` §4.
- **More:** `frontend/README.md` (the page), `frontend/API_CONTRACT.md` (the endpoints).

---

# The synthetic data

**All of this data is fake.** It is shaped like the data described in the project brief (Section 04): messy colour spellings, free-text fabric, and return comments in Hinglish with typos, mixed reasons, vague text and the odd emoji.

## What is in here

| File | What it is |
|---|---|
| `sql/01_schema.sql` | Creates all the tables. **Re-running it drops and recreates them.** |
| `sql/02_data.sql` | Inserts all the rows, in one transaction |
| `csv/*.csv` | The same data as CSV, one file per table |
| `generate_data.py` | Rebuilds everything. Fixed seed (42), so every teammate gets identical data |

## Load it in DBeaver (Postgres)

1. In DBeaver, connect to your Postgres and create an empty database, for example `dhaga`.
2. Open `sql/01_schema.sql` (File → Open File), make sure the `dhaga` connection is selected at the top, and run it as a script with **Alt+X** (Mac: **Option+X**). "Does not exist, skipping" notices on the first run are normal.
3. Open `sql/02_data.sql` the same way and run it with **Alt+X**. It is about 7 MB and takes a few seconds.
4. Check it worked: `SELECT count(*) FROM returns_enriched;` should return **6,571**.

From a terminal instead: `psql -d dhaga -f sql/01_schema.sql` and then `psql -d dhaga -f sql/02_data.sql`.

## Tables

**Source tables (what Dhaga already has):**

| Table | Rows | Notes |
|---|---|---|
| `vendors` | 40 | Mostly Tiruppur and Jaipur. Size chart and lead-time fields are deliberately inconsistent |
| `products` | 600 | 95 different colour spellings, free-text fabric |
| `customers` | 6,000 | 78% women, mostly aged 18 to 34, most in tier-2/3 cities |
| `orders` | 24,000 | Two years (Oct 2024 to Sep 2026). 61% COD. Status includes `RTO` (no return reason, by design) |
| `order_items` | 27,824 | One row per item. **Size lives here** |
| `returns` | 6,571 | `reason_dropdown` is one of six reasons or `Other`. 45% are `Other`, with free text in `other_text` |
| `reviews` | 6,000 | Star rating plus free text |

**Evaluation only:** `eval_return_labels` holds the correct issue for every return. Real Dhaga data would not have this. **Never send it to a model.** Use it only to measure your classifier's accuracy for the build note.

**Output tables (empty; your pipeline fills them):** `classified_returns`, `weekly_issue_counts`, `corrections`, `settings` (pre-filled with a 0.70 confidence threshold and a minimum of 20 returns per hotspot).

**Helper view:** `returns_enriched` joins every return to its order, size, product, vendor and city. That's pipeline step 1 done in SQL.

## How it compares with the brief

| Number | Brief | This data |
|---|---|---|
| COD share | 61% | 61% |
| COD orders that become RTO | 26% | 27% |
| Delivered orders with a return | 31% | 32% |
| Returns marked "Other" | 44% | 45% |
| Average order value | ₹840 | ₹924 |
| Volume | ~48,000 orders a week | 24,000 orders over **two years** (scaled down so it's quick to load and cheap to classify) |

## Answer key: the patterns hidden in the data

Your dashboard should find these on its own. If it doesn't, something in the pipeline is wrong. **Don't show this section in the demo.**

1. **Fit hotspot:** vendor **V07**'s kurtis and dresses run small (about 60% of their returns are "too small"), especially sizes M and L. **It gets worse in year two**, which is your year-over-year trend story.
2. **Colour hotspot:** vendor **V12**'s products don't match their photos.
3. **Quality hotspot:** vendor **V21** has thin fabric and poor stitching.
4. **Location:** north-east cities (Guwahati, Agartala, Shillong, Imphal) have more "damaged" and "delivery late" returns.
5. **Season:** more "delivery late" returns in October and November.
6. **Kidswear** leans towards "too large".

Values in `true_issue`: `too_small`, `too_large`, `colour_mismatch`, `quality`, `damaged`, `wrong_item`, `changed_mind`, `delivery_late`, `unclear`. About 6% of "Other" comments mention two reasons (`is_mixed = true`).
