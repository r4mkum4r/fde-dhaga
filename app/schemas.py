"""
Every shape that crosses the API boundary. FastAPI validates each response against
these, and generates the published schema (GET /api/openapi.json) from them, so the
docs can't drift from what the code returns.
"""
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

IssueType = Literal["too_small", "too_large", "colour_mismatch", "quality", "damaged",
                    "wrong_item", "changed_mind", "delivery_late", "unclear", "failed"]
ISSUE_DOC = ("Return reason. Same values as classified_returns.issue_type. `unclear` = no reason could be read; "
             "`failed` = the comment couldn't be processed.")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- /api/summary ----------
class Window(Strict):
    from_: str = Field(alias="from", description="First day of the window, ISO date", examples=["2025-10-01"])
    to: str = Field(description="Last day of the window, ISO date", examples=["2026-09-30"])
    label: str = Field(examples=["Oct 2025 – Sep 2026"])
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Meta(Strict):
    data_mode: Literal["sample", "live"] = Field(
        description="`sample`: 'Other' comments labelled by the keyword stand-in (pipeline hasn't run). "
                    "`live`: labels come from classified_returns.")
    synthetic: bool = Field(description="True while the database holds synthetic data. The page shows a banner.")
    generated_at: str = Field(description="When these numbers were computed, ISO datetime UTC")
    window: Window = Field(description="The latest twelve whole months in the data. Every count in the summary covers this window.")
    confidence_threshold: float = Field(description="From the settings table", examples=[0.7])
    min_returns_per_hotspot: int = Field(description="From the settings table", examples=[20])
    classifier_note: Optional[str] = Field(None, description="Shown in the banner when set")
    corrections_storage: Literal["permanent", "temporary", "browser"] = Field(
        description="`temporary` on SQLite: corrections are lost when the app restarts. "
                    "`browser` on the static site: each viewer's marks stay in their own browser")


class Headline(Strict):
    returns: int = Field(description="Returns in the window", examples=[3675])
    known_reason_before: float = Field(description="Share with a dropdown reason other than 'Other' (0–1)", examples=[0.553])
    known_reason_after: float = Field(description="Share whose issue_type isn't unclear or failed (0–1)", examples=[0.863])
    other_comments: int = Field(description="Returns whose dropdown was 'Other'", examples=[1643])
    unclear: int
    failed: int


class Driver(Strict):
    issue: IssueType = Field(description=ISSUE_DOC)
    label: str = Field(examples=["Too small"])
    count: int
    share: float = Field(description="count / headline.returns (0–1)")


class Hotspot(Strict):
    id: str = Field(description="`vendor_id|subcategory`", examples=["V07|Kurti"])
    vendor_id: str
    vendor_name: str
    subcategory: str = Field(description="Product type", examples=["Kurti"])
    size: str = Field(description="Top one or two sizes for the top issue, or `All sizes`", examples=["L, M"])
    size_breakdown: Dict[str, int] = Field(description="Returns with the top issue, by size")
    returns: int = Field(description="All returns for this vendor × product type in the window")
    top_issue: IssueType = Field(description="The issue most over-represented here against baseline_share")
    top_issue_label: str
    top_issue_count: int
    top_issue_share: float = Field(description="top_issue_count / returns (0–1)")
    baseline_share: float = Field(description="Share of top_issue across all returns (0–1)")
    unclear_or_failed: int


class Location(Strict):
    city: str
    state: str
    returns: int
    top_issue: IssueType
    top_issue_label: str
    top_issue_count: int
    top_issue_share: float
    baseline_share: float


class Summary(Strict):
    meta: Meta
    headline: Headline
    drivers: List[Driver] = Field(description="One row per issue type, including unclear and failed (count may be 0)")
    hotspots: List[Hotspot] = Field(
        description="Ranked. Vendor × product type with ≥ min_returns_per_hotspot returns, whose top issue is "
                    "≥ 5 points above baseline")
    locations: List[Location] = Field(description="Ranked. Cities with ≥ 40 returns whose top issue is ≥ 5 points above baseline")


# ---------- /api/trend ----------
class TrendSeries(Strict):
    key: Literal["last_year", "this_year"]
    label: str = Field(examples=["Oct 2025 – Sep 2026"])
    counts: List[int] = Field(min_length=12, max_length=12, description="Returns per month, aligned with `months`")


class Trend(Strict):
    issue: IssueType
    label: str
    months: List[str] = Field(min_length=12, max_length=12, examples=[["Oct", "Nov", "Dec", "Jan", "Feb", "Mar",
                                                                          "Apr", "May", "Jun", "Jul", "Aug", "Sep"]])
    series: List[TrendSeries] = Field(min_length=2, max_length=2)


# ---------- /api/returns ----------
class ReturnRow(Strict):
    return_id: str = Field(examples=["RT000139"])
    return_date: str = Field(description="ISO date")
    sku: str
    product_name: str
    subcategory: str
    department: str
    vendor_id: str
    vendor_name: str
    size: str
    city: str
    state: str
    reason_dropdown: str = Field(description="What the customer picked in the app, e.g. `Other`")
    comment: Optional[str] = Field(description="Customer's free text. Untrusted: escape before rendering. Null when the dropdown was used")
    issue_type: IssueType = Field(description=ISSUE_DOC)
    confidence: Optional[float] = Field(description="0–1. Null for gate and failed rows")
    evidence_phrase: Optional[str] = Field(description="Always an exact (case-insensitive) substring of `comment`, or null")
    source: str = Field(description="`dropdown` | `gate` | `cheap_model` | `strong_model` | `llm` (AI, model tier not recorded); "
                                    "`sample_stub` / `sample_stub_low_conf` before the AI has run; `pipeline` when it skipped this row")
    model_name: Optional[str]
    error: Optional[str] = Field(description="Why it failed or couldn't be read, shown on screen")
    explanation: Optional[str] = Field(None, description="The AI's own one-line reason for the label, in English. Null for dropdown, gate and keyword-match rows")


class ReturnsPage(Strict):
    total: int
    returns: List[ReturnRow]


# ---------- /api/corrections ----------
class CorrectionIn(Strict):
    return_id: str = Field(examples=["RT000556"])
    model_issue_type: IssueType = Field(description="The label Neha is judging")
    is_correct: bool
    corrected_issue: Optional[IssueType] = Field(None, description="Optional, only when is_correct is false")


class Mark(Strict):
    return_id: str
    model_issue_type: Optional[str]
    is_correct: bool
    corrected_issue: Optional[str]
    corrected_at: str


class Accuracy(Strict):
    reviewed: int = Field(description="Returns Neha has marked (latest mark per return)")
    correct: int


class Corrections(Strict):
    corrections: Dict[str, Mark] = Field(description="Latest mark per return_id")
    accuracy: Accuracy


class CorrectionSaved(Strict):
    ok: Literal[True]
    accuracy: Accuracy
    storage: Literal["permanent", "temporary"]


# ---------- /health ----------
class Health(Strict):
    status: Literal["ok"]
    database: str = Field(examples=["Postgres db.example.com:5432/dhaga"])
    tables: Dict[str, int] = Field(description="Row counts")
    labels_from: Literal["sample_stub", "pipeline"]
    corrections_storage: Literal["permanent", "temporary"]
    build: str = Field(description="Deployed commit SHA, or `local`")


class HealthDown(Strict):
    status: Literal["down"]
    database: str
    error: str


class ErrorOut(BaseModel):
    detail: str | list = Field(description="A sentence safe to show on screen (422: a list of validation errors)")
