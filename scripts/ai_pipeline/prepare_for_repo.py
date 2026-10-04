import os
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

INPUT = "csv/classified_returns_with_dropdown.csv"
OUTPUT = "csv/classified_returns.csv"

os.makedirs("csv", exist_ok=True)
df = pd.read_csv(INPUT)

required = {"return_id", "other_text", "issue", "explanation", "model"}
missing = required - set(df.columns)
if missing:
    raise ValueError(f"Input is missing columns: {sorted(missing)}")

# Evidence must be copied verbatim from the original comment, not generated.
def evidence_from_comment(row):
    comment = "" if pd.isna(row["other_text"]) else str(row["other_text"]).strip()
    if not comment:
        return ""
    explanation = "" if pd.isna(row["explanation"]) else str(row["explanation"]).strip()
    # Only use the comment itself as evidence. The full original comment is safe
    # and verbatim; downstream UI can display it or truncate it.
    return comment

models = df["model"].fillna(os.getenv("MODEL", "unknown"))
models = models.replace("", os.getenv("MODEL", "unknown"))

repo = pd.DataFrame({
    "return_id": df["return_id"].astype(str),
    "issue_type": df["issue"].astype(str),
    # Classifier does not currently emit confidence; do not fabricate one.
    "confidence": pd.NA,
    "evidence_phrase": df.apply(evidence_from_comment, axis=1),
    "source": models.apply(
        lambda m: "strong_model" if "claude" in str(m).lower() else "cheap_model"
    ),
    "model_name": models,
})

allowed = {
    "too_small", "too_large", "colour_mismatch", "quality", "damaged",
    "wrong_item", "changed_mind", "delivery_late", "unclear", "failed"
}
invalid = sorted(set(repo["issue_type"].dropna()) - allowed)
if invalid:
    raise ValueError(f"Invalid issue_type values: {invalid}")

if repo["return_id"].duplicated().any():
    raise ValueError("Duplicate return_id values found")

repo.to_csv(OUTPUT, index=False)
print(f"Saved {OUTPUT} ({len(repo)} rows)")
print(f"Missing confidence values: {repo['confidence'].isna().sum()} (not generated)")
