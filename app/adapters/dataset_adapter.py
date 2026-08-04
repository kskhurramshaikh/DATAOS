# Dataset Adapter -- Bronze / Silver / Gold
#
# This is the only file in the codebase that knows the storage layout of
# the medallion architecture. The router calls run(payload) and gets
# back a plain dict describing what happened -- it never touches the
# filesystem layout directly, same invisibility contract every adapter
# in DataOS 2.0 holds.
#
# For this first pass: local disk under data/{bronze,silver,gold}/, not
# a managed object store. That's a deliberate, disclosed tradeoff for
# today's demo -- on Render's free tier this is ephemeral (wiped on
# redeploy), same caveat as the chat auth database. Swapping this for a
# real S3-compatible bucket is a storage-layer change only; nothing
# above this file needs to know about it when that happens.
#
# Bronze: the raw upload, untouched.
# Silver: exact duplicate rows removed, nulls reported (not invented --
#         no values are guessed or filled in).
# Gold: the cleaned data plus a lightweight profile (numeric summaries,
#       top categories) that a BI tool or another capability could read
#       without having to re-derive it.

import csv
import io
import os
from datetime import datetime, timezone

import pandas as pd

DATA_ROOT = os.environ.get("DATAOS_DATA_ROOT", "data")


def _safe_name(name: str) -> str:
    keep = [c if (c.isalnum() or c in ("-", "_")) else "_" for c in name.strip()]
    cleaned = "".join(keep).strip("_")
    return cleaned or "unnamed_dataset"


def _numeric_summary(df: pd.DataFrame) -> dict:
    numeric_cols = df.select_dtypes(include="number").columns
    summary = {}
    for col in numeric_cols:
        series = df[col].dropna()
        if series.empty:
            continue
        summary[col] = {
            "sum": round(float(series.sum()), 2),
            "mean": round(float(series.mean()), 2),
            "min": round(float(series.min()), 2),
            "max": round(float(series.max()), 2),
        }
    return summary


def _top_categories(df: pd.DataFrame, max_unique: int = 20, max_cols: int = 5) -> dict:
    result = {}
    non_numeric_cols = df.select_dtypes(exclude="number").columns
    for col in list(non_numeric_cols)[:max_cols]:
        counts = df[col].value_counts(dropna=True)
        if 0 < len(counts) <= max_unique:
            result[col] = {str(k): int(v) for k, v in counts.head(5).items()}
    return result


def run(payload: dict) -> dict:
    dataset_name = payload.get("dataset_name")
    csv_content = payload.get("csv_content")

    if not dataset_name:
        raise ValueError("dataset_name is required to add a dataset.")
    if not csv_content:
        raise ValueError("csv_content is required -- no file content was received.")

    safe_name = _safe_name(dataset_name)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    try:
        df = pd.read_csv(io.StringIO(csv_content))
    except (pd.errors.ParserError, csv.Error, UnicodeDecodeError) as e:
        raise ValueError(f"Could not parse the uploaded file as CSV: {e}")

    if df.empty:
        raise ValueError("The uploaded file parsed as CSV but contains no rows.")

    # --- Bronze: raw, untouched -------------------------------------
    bronze_dir = os.path.join(DATA_ROOT, "bronze", safe_name)
    os.makedirs(bronze_dir, exist_ok=True)
    bronze_path = os.path.join(bronze_dir, f"{timestamp}_raw.csv")
    with open(bronze_path, "w", encoding="utf-8") as f:
        f.write(csv_content)

    # --- Silver: duplicates removed, nulls reported not invented ----
    original_rows = len(df)
    null_counts_before = df.isnull().sum()
    silver_df = df.drop_duplicates()
    duplicate_rows_removed = original_rows - len(silver_df)

    silver_dir = os.path.join(DATA_ROOT, "silver", safe_name)
    os.makedirs(silver_dir, exist_ok=True)
    silver_path = os.path.join(silver_dir, "cleaned.csv")
    silver_df.to_csv(silver_path, index=False)

    # --- Gold: cleaned data + a lightweight profile ------------------
    gold_dir = os.path.join(DATA_ROOT, "gold", safe_name)
    os.makedirs(gold_dir, exist_ok=True)
    gold_path = os.path.join(gold_dir, "data.csv")
    silver_df.to_csv(gold_path, index=False)

    null_counts = {
        col: int(cnt) for col, cnt in null_counts_before.items() if cnt > 0
    }

    return {
        "dataset_name": safe_name,
        "rows": len(silver_df),
        "columns": list(silver_df.columns),
        "duplicate_rows_removed": int(duplicate_rows_removed),
        "null_counts": null_counts,
        "numeric_summary": _numeric_summary(silver_df),
        "top_categories": _top_categories(silver_df),
        "storage": {"bronze": bronze_path, "silver": silver_path, "gold": gold_path},
    }
