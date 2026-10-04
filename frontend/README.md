# Frontend: Neha's "Why products come back" dashboard

Plain HTML, CSS and JavaScript. There is no build step and nothing to install.

## Run it

Normally the FastAPI app serves this page. See the root README: `uvicorn app.main:app --port 8000`.

To work on the frontend alone, without the API, use the sample files:

```bash
python3 scripts/build_sample_data.py              # only if frontend/sample/ is missing or the rules changed
python3 -m http.server 5173 --directory frontend
```

Then open http://localhost:5173/?mode=sample. Opening `index.html` straight from disk won't work, because
the browser blocks `fetch` on `file://` URLs.

## What's on the page

| Part | Answers | Click it |
|---|---|---|
| What to fix first | What should Neha act on? Plain sentences built from the ranked problem spots, each with a suggested next step (Neha decides) | "Read the comments" opens the customer comments |
| At Dhaga's scale | The same problem in the brief's numbers: 14,880 returns and 6,547 unread "Other" comments a week, with the arithmetic shown | Hover a tag to see the citation |
| Headline tiles | How many returns? How many have a known reason now? How many couldn't be classified? How accurate are the labels? | "Read them" / "See which" opens the unclear or failed comments |
| Return drivers | What is causing the most returns? Unclear and failed are always shown as their own bars | A reason switches the trend to it; unclear/failed opens those comments |
| Problem spots | Which vendor, product type and size stand out? | A row opens the customer comments behind it |
| Locations | Which city has which problem, compared with everywhere else? | A row opens that city's comments |
| Trend | Is it getting better? This year against last year, by month | Hover for monthly values |
| Comments drawer | The real comments, with the label, confidence and the phrase it was based on | ✓ / ✗ marks each label right or wrong; after ✗, "Should be…" records the right one |

## Where every number comes from

Every number has a tag: **Brief** (the client gave it to us), **Calc** (arithmetic on brief numbers),
**Estimate** (a rate measured on the sample, applied to Dhaga's volume) or **Sample** (measured on the
synthetic data). This answers the brief's question "which numbers came from the case study, and which did
you estimate?" before anyone in the room asks it.

## Failing visibly

- **Unclear** (yellow, `?`) and **failed** (red, `✕`) results get their own tile, their own bars, and their
  own views. They are never folded into a reason or hidden.
- If data doesn't load, the page says which request failed and why, hides the numbers, and doesn't show
  stale ones. The trend and the comments drawer each fail on their own without taking the page down.
- If a ✓ / ✗ doesn't save, the card says "Not saved" with the reason.

## Sample mode vs live mode

The sample files are produced by the same rules as the API (`app/analytics.py`), and they're labelled as sample data on screen. The
"Other" comments in them are labelled by a **keyword stand-in, not the real models**, so the label
accuracy you see in sample mode says nothing about the real classifier. In sample mode, ✓ / ✗ marks
are saved in your browser only.

The page uses the API by default (`DATA_MODE_DEFAULT = "api"` in `js/api.js`). Add `?mode=sample` to use the
files in `sample/` instead. In API mode, ✓ / ✗ marks go to the `corrections` table. On SQLite (no
`DATABASE_URL`), the page says they last until the app restarts.

## Files

| File | What it does |
|---|---|
| `index.html` | Page layout |
| `css/styles.css` | Colours (light and dark), layout, and the chart and badge styles |
| `js/brief.js` | The numbers Dhaga gave in the brief, each with its citation, plus the open questions for the client. Nothing invented goes here |
| `js/api.js` | **The only file that fetches data.** Switches between sample files and the API |
| `js/app.js` | Draws the tiles, charts, tables and drawer, and handles ✓ / ✗ |
| `sample/*.json` | Sample responses, made by `scripts/build_sample_data.py` from `csv/` |
| `API_CONTRACT.md` | The JSON each endpoint must return |

## Known gaps

- The data starts on 7 Oct 2024, so October "last year" in the trend is missing its first week.
- Locations are a ranked table, not a map.
- There's no date picker. The window is fixed to the latest 12 months, which the API decides.
