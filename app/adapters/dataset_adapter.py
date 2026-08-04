# Dataset Adapter -- Bronze / Silver / Gold
#
# This is the only file in the codebase that knows the storage layout of
# the medallion architecture. The router calls run(payload) and gets
# back a plain dict describing what happened -- it never touches the
# filesystem layout directly, same invisibility contract every adapter
# in DataOS 2.0 holds.
#
# For this pass: local disk under data/{bronze,silver,gold}/, not a
# managed object store. That's a deliberate, disclosed tradeoff for the
# demo phase -- on Render's free tier this is ephemeral (wiped on
# redeploy), same caveat as the chat auth database. Swapping this for a
# real S3-compatible bucket is a storage-layer change only; nothing
# above this file needs to know about it when that happens.
#
# Bronze: the raw upload, untouched.
# Silver: exact duplicate rows removed, nulls reported (not invented --
#         no values are guessed or filled in).
# Gold: curated for business use -- columns that are mostly empty
#       (over 50% null) are dropped rather than carried forward
#       unreliable, plus a numeric/category profile.
#
# Promotion gating: if ANY column in Silver is more than 10% null, the
# dataset is held at "silver_held" rather than auto-promoted -- data
# that shaky shouldn't silently become "business-ready" without a
# human or agent deciding that's acceptable. Call promote_dataset() to
# push it through anyway once someone's made that call.
#
# The three landing/cleaning/promoting steps are split into their own
# functions (rather than folded into run()) specifically so the
# streaming chat endpoint can call them one at a time and report real
# progress between them, not synthetic delays.

import csv
import io
import json
import os
from datetime import datetime, timezone

import pandas as pd

from app.db import get_conn

DATA_ROOT = os.environ.get("DATAOS_DATA_ROOT", "data")
NULL_RATE_HOLD_THRESHOLD = 0.10  # >10% null in any column holds at Silver
GOLD_DROP_COLUMN_THRESHOLD = 0.50  # >50% null in a column drops it from Gold


def _safe_name(name: str) -> str:
    keep = [c if (c.isalnum() or c in ("-", "_")) else "_" for c in name.strip()]
    cleaned = "".join(keep).strip("_")
    return cleaned or "unnamed_dataset"


def _read_sheet_with_header_detection(raw_bytes: bytes, sheet_name: str) -> pd.DataFrame:
    """
    Real-world exported workbooks often have a title/report-name row (or
    two) sitting above the actual column headers -- a merged "Report
    Name" cell, a generation-date line, etc. Naively reading with
    header=0 in that case grabs the title as the header and produces
    "Unnamed: N" columns with the real headers stuck in row 1 as data.

    Try the first several rows as the header candidate and keep whichever
    produces the fewest "Unnamed" columns -- that's the real header row.
    """
    best_df = None
    best_unnamed_ratio = 1.0

    for header_row in range(5):
        try:
            df = pd.read_excel(
                io.BytesIO(raw_bytes), sheet_name=sheet_name, header=header_row, engine="openpyxl"
            )
        except Exception:
            continue
        if df.empty or len(df.columns) == 0:
            continue

        unnamed_count = sum(1 for c in df.columns if str(c).startswith("Unnamed"))
        unnamed_ratio = unnamed_count / len(df.columns)

        if unnamed_ratio < best_unnamed_ratio:
            best_unnamed_ratio = unnamed_ratio
            best_df = df
        if unnamed_ratio == 0:
            break

    if best_df is None:
        raise ValueError(f"Could not find a usable header row in sheet '{sheet_name}'.")

    # Drop rows that are entirely empty -- can happen when a stray blank
    # row sits between the detected header and the real data.
    return best_df.dropna(how="all").reset_index(drop=True)


def extract_csv_content(filename: str, raw_bytes: bytes) -> tuple[str, str | None]:
    """
    Accepts raw upload bytes and returns (csv_content, sheet_used).

    CSV passes through as-is (decoded UTF-8). Excel (.xlsx/.xls) is read
    via pandas/openpyxl -- if the workbook has multiple sheets, one whose
    name contains "customer" is preferred, since that's the raw
    transactional data a medallion pipeline actually ingests. Scorecard/
    summary sheets in the same workbook (an assessment scorecard, an
    executive rollup) aren't meant to land through Bronze/Silver/Gold the
    same way real records are -- they're computed outputs, not source
    data. Falls back to the first sheet if no "customer" match exists.

    Header row is auto-detected per _read_sheet_with_header_detection --
    see that function for why this matters.
    """
    is_excel = filename.lower().endswith((".xlsx", ".xls"))

    if not is_excel:
        try:
            return raw_bytes.decode("utf-8"), None
        except UnicodeDecodeError as e:
            raise ValueError(f"Could not read the file as UTF-8 text: {e}")

    try:
        sheet_names = pd.ExcelFile(io.BytesIO(raw_bytes), engine="openpyxl").sheet_names
    except Exception as e:
        raise ValueError(f"Could not read the Excel file: {e}")

    if not sheet_names:
        raise ValueError("The Excel file has no sheets.")

    sheet_name = next(
        (name for name in sheet_names if "customer" in name.lower()),
        sheet_names[0],
    )

    df = _read_sheet_with_header_detection(raw_bytes, sheet_name)
    return df.to_csv(index=False), sheet_name


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


def _upsert_dataset_record(
    safe_name: str,
    display_name: str,
    uploaded_by: int,
    stage: str,
    rows: int,
    columns: list[str],
    dropped_columns: list[str],
    duplicate_rows_removed: int,
    null_counts: dict,
    bronze_path: str,
    silver_path: str | None,
    gold_path: str | None,
):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM datasets WHERE safe_name = ?", (safe_name,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE datasets SET display_name=?, uploaded_by=?, stage=?, rows=?,
                   columns_json=?, dropped_columns_json=?, duplicate_rows_removed=?,
                   null_counts_json=?, bronze_path=?, silver_path=?, gold_path=?,
                   updated_at=? WHERE safe_name=?""",
                (
                    display_name, uploaded_by, stage, rows,
                    json.dumps(columns), json.dumps(dropped_columns), duplicate_rows_removed,
                    json.dumps(null_counts), bronze_path, silver_path, gold_path,
                    now, safe_name,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO datasets
                   (safe_name, display_name, uploaded_by, stage, rows, columns_json,
                    dropped_columns_json, duplicate_rows_removed, null_counts_json,
                    bronze_path, silver_path, gold_path, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    safe_name, display_name, uploaded_by, stage, rows,
                    json.dumps(columns), json.dumps(dropped_columns), duplicate_rows_removed,
                    json.dumps(null_counts), bronze_path, silver_path, gold_path, now,
                ),
            )
        conn.commit()


# ---------------------------------------------------------------------
# The three real stages
# ---------------------------------------------------------------------

def land_bronze(dataset_name: str, csv_content: str) -> dict:
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

    bronze_dir = os.path.join(DATA_ROOT, "bronze", safe_name)
    os.makedirs(bronze_dir, exist_ok=True)
    bronze_path = os.path.join(bronze_dir, f"{timestamp}_raw.csv")
    with open(bronze_path, "w", encoding="utf-8") as f:
        f.write(csv_content)

    return {
        "safe_name": safe_name,
        "display_name": dataset_name,
        "df": df,
        "bronze_path": bronze_path,
    }


def clean_to_silver(safe_name: str, df: pd.DataFrame) -> dict:
    null_counts_before = df.isnull().sum()
    silver_df = df.drop_duplicates()
    duplicate_rows_removed = len(df) - len(silver_df)

    silver_dir = os.path.join(DATA_ROOT, "silver", safe_name)
    os.makedirs(silver_dir, exist_ok=True)
    silver_path = os.path.join(silver_dir, "cleaned.csv")
    silver_df.to_csv(silver_path, index=False)

    null_counts = {col: int(cnt) for col, cnt in null_counts_before.items() if cnt > 0}
    rows = len(silver_df)

    # A column empty enough to be dropped at Gold anyway (>50% null)
    # doesn't need to hold up promotion -- it'll just be curated away.
    # The hold is for the ambiguous middle ground: too null to trust
    # silently, not empty enough to just drop.
    relevant_rates = {
        col: (cnt / rows)
        for col, cnt in null_counts.items()
        if rows and (cnt / rows) <= GOLD_DROP_COLUMN_THRESHOLD
    }
    max_null_rate = max(relevant_rates.values(), default=0.0)
    held = max_null_rate > NULL_RATE_HOLD_THRESHOLD

    return {
        "silver_df": silver_df,
        "silver_path": silver_path,
        "duplicate_rows_removed": int(duplicate_rows_removed),
        "null_counts": null_counts,
        "held": held,
        "hold_reason": (
            f"a column is {max_null_rate:.0%} null, over the {NULL_RATE_HOLD_THRESHOLD:.0%} auto-promotion threshold"
            if held else None
        ),
    }


def promote_to_gold(safe_name: str, silver_df: pd.DataFrame) -> dict:
    rows = len(silver_df)
    dropped_columns = []
    curated_df = silver_df
    if rows:
        null_fraction = silver_df.isnull().mean()
        dropped_columns = list(null_fraction[null_fraction > GOLD_DROP_COLUMN_THRESHOLD].index)
        if dropped_columns:
            curated_df = silver_df.drop(columns=dropped_columns)

    gold_dir = os.path.join(DATA_ROOT, "gold", safe_name)
    os.makedirs(gold_dir, exist_ok=True)
    gold_path = os.path.join(gold_dir, "data.csv")
    curated_df.to_csv(gold_path, index=False)

    return {
        "gold_path": gold_path,
        "dropped_columns": dropped_columns,
        "numeric_summary": _numeric_summary(curated_df),
        "top_categories": _top_categories(curated_df),
    }


# ---------------------------------------------------------------------
# Public capability entry points (used by the router / raw /intent path)
# ---------------------------------------------------------------------

def run(payload: dict) -> dict:
    """add_dataset: land Bronze -> Silver, then Gold unless held for
    quality gating. Used by the raw /intent endpoint and by tests; the
    streaming chat endpoint calls the three stage functions directly
    instead, to report real progress between them."""
    dataset_name = payload.get("dataset_name")
    csv_content = payload.get("csv_content")
    uploaded_by = payload.get("uploaded_by", 0)

    bronze = land_bronze(dataset_name, csv_content)
    silver = clean_to_silver(bronze["safe_name"], bronze["df"])

    result = {
        "dataset_name": bronze["safe_name"],
        "rows": len(silver["silver_df"]),
        "columns": list(silver["silver_df"].columns),
        "duplicate_rows_removed": silver["duplicate_rows_removed"],
        "null_counts": silver["null_counts"],
        "storage": {"bronze": bronze["bronze_path"], "silver": silver["silver_path"], "gold": None},
    }

    if silver["held"]:
        result["stage"] = "silver_held"
        result["hold_reason"] = silver["hold_reason"]
        result["numeric_summary"] = {}
        result["top_categories"] = {}
        result["dropped_columns"] = []
        _upsert_dataset_record(
            bronze["safe_name"], bronze["display_name"], uploaded_by, "silver_held",
            result["rows"], result["columns"], [], result["duplicate_rows_removed"],
            result["null_counts"], bronze["bronze_path"], silver["silver_path"], None,
        )
    else:
        gold = promote_to_gold(bronze["safe_name"], silver["silver_df"])
        result["stage"] = "gold"
        result["numeric_summary"] = gold["numeric_summary"]
        result["top_categories"] = gold["top_categories"]
        result["dropped_columns"] = gold["dropped_columns"]
        result["storage"]["gold"] = gold["gold_path"]
        _upsert_dataset_record(
            bronze["safe_name"], bronze["display_name"], uploaded_by, "gold",
            result["rows"], result["columns"], gold["dropped_columns"],
            result["duplicate_rows_removed"], result["null_counts"],
            bronze["bronze_path"], silver["silver_path"], gold["gold_path"],
        )

    return result


def list_datasets(payload: dict) -> dict:
    """list_datasets: returns every dataset's current stage/status, or
    one dataset's detail if dataset_name is given in the payload."""
    dataset_name = payload.get("dataset_name")
    with get_conn() as conn:
        if dataset_name:
            safe_name = _safe_name(dataset_name)
            rows = conn.execute(
                "SELECT * FROM datasets WHERE safe_name = ?", (safe_name,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM datasets ORDER BY updated_at DESC").fetchall()

    datasets = []
    for r in rows:
        datasets.append({
            "dataset_name": r["safe_name"],
            "display_name": r["display_name"],
            "stage": r["stage"],
            "rows": r["rows"],
            "columns": json.loads(r["columns_json"]),
            "dropped_columns": json.loads(r["dropped_columns_json"]),
            "duplicate_rows_removed": r["duplicate_rows_removed"],
            "null_counts": json.loads(r["null_counts_json"]),
            "updated_at": r["updated_at"],
        })

    if dataset_name and not datasets:
        raise ValueError(f"No dataset found matching '{dataset_name}'.")

    return {"count": len(datasets), "datasets": datasets}


def promote_dataset(payload: dict) -> dict:
    """promote_dataset: force a dataset held at Silver (due to quality
    gating) through to Gold anyway. No-op with a clear message if the
    dataset isn't actually held."""
    dataset_name = payload.get("dataset_name")
    if not dataset_name:
        raise ValueError("dataset_name is required to promote a dataset.")

    safe_name = _safe_name(dataset_name)
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM datasets WHERE safe_name = ?", (safe_name,)).fetchone()

    if row is None:
        raise ValueError(f"No dataset found matching '{dataset_name}'.")
    if row["stage"] != "silver_held":
        return {
            "dataset_name": safe_name,
            "already_stage": row["stage"],
            "message": f"'{safe_name}' is already at stage '{row['stage']}', not held -- nothing to promote.",
        }

    silver_df = pd.read_csv(row["silver_path"])
    gold = promote_to_gold(safe_name, silver_df)

    _upsert_dataset_record(
        safe_name, row["display_name"], row["uploaded_by"], "gold",
        row["rows"], json.loads(row["columns_json"]), gold["dropped_columns"],
        row["duplicate_rows_removed"], json.loads(row["null_counts_json"]),
        row["bronze_path"], row["silver_path"], gold["gold_path"],
    )

    return {
        "dataset_name": safe_name,
        "stage": "gold",
        "dropped_columns": gold["dropped_columns"],
        "numeric_summary": gold["numeric_summary"],
        "top_categories": gold["top_categories"],
    }
