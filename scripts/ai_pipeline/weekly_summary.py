import os
import pandas as pd

INPUT = "csv/classified_returns.csv"
OUTPUT = "csv/weekly_summary.csv"

os.makedirs("csv", exist_ok=True)
df = pd.read_csv(INPUT)

required = {"issue_type"}
missing = required - set(df.columns)
if missing:
    raise ValueError(f"Input is missing columns: {sorted(missing)}")

summary = (
    df.groupby("issue_type", dropna=False)
      .size()
      .reset_index(name="return_count")
      .sort_values("return_count", ascending=False)
)
total = int(summary["return_count"].sum())
summary["percentage"] = (summary["return_count"] / total * 100).round(1)

summary.to_csv(OUTPUT, index=False)
print(f"Saved {OUTPUT}")
print(summary.to_string(index=False))
