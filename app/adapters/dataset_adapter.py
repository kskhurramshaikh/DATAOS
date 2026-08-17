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
# CONFIRMED LIVE (2026-08-17): unlike the chat auth database (now on
# Postgres, see app/db.py), this file's local-disk tradeoff is STILL
# ACTIVE -- a redeploy after uploading a dataset wipes its Bronze/
# Silver/Gold files while the dataset's Postgres row (now durable)
# survives, leaving a real record pointing at a missing file. Confirmed
# directly: read_silver_csv() below hit a raw FileNotFoundError against
# the live app after a canary redeploy. Now caught there and surfaced
# as a clear ValueError instead of a raw traceback -- but the
# underlying limitation (files not yet moved to SeaweedFS, which this
# same app is already wired to for the Lakehouse dashboard) is still
# open, pending a decision on whether it's worth the larger file-I/O
# rewrite this file's own docstring already anticipated above.
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
NULL_RATE_HOLD_THRESHOLD = 0.10  # >10% null in any column holds at Silver -- for normal-sized datasets
GOLD_DROP_COLUMN_THRESHOLD = 0.50  # >50% null in a column drops it from Gold
# A small reference/lookup table (e.g. a 7-row LGD rate table) hits a much
# higher percentage from a single missing value than a large customer
# dataset does -- 1 missing value in 7 rows is already 14%, which would
# wrongly hold a table that's actually fine. Real reference tables like
# this are common (parameter files, rate lookups, scenario definitions)
# and a single cosmetic gap in one shouldn't block promotion the way the
# same rate would for a 600-row customer file. Found via Dr. Saber's own
# testing -- see change log.
SMALL_TABLE_ROW_THRESHOLD = 20
SMALL_TABLE_NULL_RATE_HOLD_THRESHOLD = 0.35


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


def list_excel_sheet_names(raw_bytes: bytes) -> list[str]:
    """List sheet names in an Excel workbook without fully reading any of them."""
    try:
        return pd.ExcelFile(io.BytesIO(raw_bytes), engine="openpyxl").sheet_names
    except Exception:
        return []


def extract_specific_sheet_csv(raw_bytes: bytes, sheet_name: str) -> str:
    """Read one named sheet (with header-row auto-detection) and return it as CSV text."""
    df = _read_sheet_with_header_detection(raw_bytes, sheet_name)
    return df.to_csv(index=False)


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


IDENTIFIER_COL_KEYWORDS = [
    "id", "phone", "mobile", "national_id", "cust_id", "customer_id", "account",
    "iban", "zip", "postal", "ssn", "reference", "code", "number",
]


def _looks_like_identifier(col_name: str) -> bool:
    low = str(col_name).lower().replace(" ", "_")
    return any(kw == low or low.startswith(kw + "_") or low.endswith("_" + kw) or low == kw for kw in IDENTIFIER_COL_KEYWORDS)


def _numeric_summary(df: pd.DataFrame) -> dict:
    """
    Only summarizes columns that are genuinely quantitative (like
    PRODUCTS -- sum/average makes sense). Numeric-looking identifier
    columns (National ID, phone, customer ID) are excluded on purpose --
    summing or averaging an ID is meaningless and looks like a bug if it
    shows up in a live demo. This is a name-based heuristic, not
    semantic understanding -- it can miss an oddly-named identifier
    column, but it fails toward omitting a summary rather than
    presenting a nonsensical one.
    """
    numeric_cols = df.select_dtypes(include="number").columns
    summary = {}
    for col in numeric_cols:
        if _looks_like_identifier(col):
            continue
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

    hold_threshold = (
        SMALL_TABLE_NULL_RATE_HOLD_THRESHOLD if rows < SMALL_TABLE_ROW_THRESHOLD else NULL_RATE_HOLD_THRESHOLD
    )

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
    held = max_null_rate > hold_threshold

    small_table_note = f" (small-table threshold, {rows} rows)" if rows < SMALL_TABLE_ROW_THRESHOLD else ""

    return {
        "silver_df": silver_df,
        "silver_path": silver_path,
        "duplicate_rows_removed": int(duplicate_rows_removed),
        "null_counts": null_counts,
        "held": held,
        "hold_reason": (
            f"a column is {max_null_rate:.0%} null, over the {hold_threshold:.0%} auto-promotion threshold{small_table_note}"
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


def read_silver_csv(safe_name: str) -> str:
    """Reads back the already-landed Silver CSV for a dataset that was
    uploaded earlier. Lets a follow-up action (like running duplicate
    detection after the fact) reuse already-cleaned data on disk instead
    of needing the original file re-uploaded.

    KNOWN LIMITATION (confirmed live, 2026-08-17): the dataset's
    Postgres row is durable (see app/db.py), but the actual Bronze/
    Silver/Gold files are still on local disk -- ephemeral, wiped on
    redeploy, per this module's own header docstring. So a redeploy
    after an upload can leave a real, correctly-persisted dataset
    record pointing at a file that no longer exists. Caught here and
    turned into a clear ValueError (surfaced as a 400, same pattern as
    every other error in this file) instead of a raw FileNotFoundError
    leaking to the UI."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT silver_path FROM datasets WHERE safe_name = ?", (safe_name,)
        ).fetchone()
    if row is None or not row["silver_path"]:
        raise ValueError(f"No landed Silver data found for dataset '{safe_name}'.")
    try:
        with open(row["silver_path"], "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise ValueError(
            f"'{safe_name}' is recorded as uploaded, but its underlying file is missing "
            f"(dataset files still live on ephemeral local disk, separate from the "
            f"database record which does persist -- likely wiped by a redeploy since "
            f"upload). Re-upload '{safe_name}' to continue."
        )


def mark_duplicate_check_run(safe_name: str) -> None:
    """Records that duplicate detection was actually run for this
    dataset, regardless of outcome (found clusters, found none, or
    wasn't applicable). This is the only reliable way to distinguish
    'genuinely resolved, 0 pending' from 'never checked, 0 rows exist
    because nothing was ever run' -- both look identical if you only
    look at the duplicate_clusters table."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE datasets SET duplicate_check_last_run_at = CURRENT_TIMESTAMP WHERE safe_name = ?",
            (safe_name,),
        )
        conn.commit()


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
            "duplicate_check_last_run_at": r["duplicate_check_last_run_at"],
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


def get_followup_recommendations(dataset_name: str, raw_bytes: bytes | None, exclude_action: str | None = None) -> list[dict]:
    """The standing set of 'what would you like to do next' chips for a
    dataset -- shared by both the chip-click path (main.py) and the
    natural-language chat path (interpreter.py), so a user isn't left
    at a dead end after completing an action on either path. Lives
    here (not in main.py) specifically so both paths import the same
    function rather than each having their own copy that can drift
    apart -- confirm_all_duplicates via typed chat offered no
    follow-up chips at all before this existed, since interpreter.py
    never had its own version of this logic to begin with.
    exclude_action omits whichever capability just ran."""
    from app.adapters import dedup_adapter  # local import: avoids a module-load-order dependency, mirrors dedup_adapter's own pattern

    recommendations = []
    if raw_bytes:
        try:
            sheet_names = list_excel_sheet_names(raw_bytes)
        except Exception:
            sheet_names = []
        ndi_applicable = any("ndi" in s.lower() for s in sheet_names)
        ifrs_applicable = any("ifrs" in s.lower() for s in sheet_names)
    else:
        # No file content available to check sheet names against --
        # only the chip-click path re-attaches raw bytes; the
        # natural-language chat path never has them (see interpreter.py).
        # Rather than silently hiding these chips whenever that happens,
        # offer them optimistically: the frontend still has the original
        # file cached (lastUploadedFile) and will correctly re-attach it
        # when clicked. Worst case is a graceful "no matching sheet"
        # error on a dataset that genuinely doesn't have one -- not a
        # missing chip a user has to re-upload to reach.
        ndi_applicable = True
        ifrs_applicable = True

    if exclude_action != "assess_ndi" and ndi_applicable:
        recommendations.append({
            "action": "assess_ndi",
            "label": "📊 Assess NDI readiness",
            "description": "Compute a data-governance readiness reading from the NDI sheet in this workbook.",
        })
    if exclude_action not in ("select_ifrs9_scenario", "compute_ifrs9") and ifrs_applicable:
        recommendations.append({
            "action": "select_ifrs9_scenario",
            "label": "💰 Compute IFRS 9 (PD, LGD & ECL)",
            "description": "Model probability of default, loss given default, and expected credit loss -- pick a macro scenario to model.",
        })
    if exclude_action != "find_duplicates":
        try:
            silver_csv = read_silver_csv(dataset_name)
            if dedup_adapter.is_applicable(silver_csv):
                recommendations.append({
                    "action": "find_duplicates",
                    "label": "🔁 Find duplicate customers",
                    "description": "Check this dataset for near-duplicate customer records needing review.",
                })
        except ValueError:
            pass
    if exclude_action != "sama_compliance":
        recommendations.append({
            "action": "sama_compliance",
            "label": "🏦 SAMA compliance status",
            "description": "Check data-governance, quality, and risk-data compliance signals against SAMA's compliance domains.",
        })
    if exclude_action != "customer_360":
        recommendations.append({
            "action": "customer_360",
            "label": "👤 Customer 360 view",
            "description": "See golden-record and data-quality KPIs for this dataset's customer base.",
        })
    return recommendations
