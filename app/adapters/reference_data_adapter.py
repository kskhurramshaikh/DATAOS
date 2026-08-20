# Reference Data Management (2026-08-20) -- Item A from the
# baj-dashboard reference-platform review. Scoped and approved by
# Khurram: a new MDM section, real Postgres-backed (not the reference
# platform's localStorage-only version), same shape as their own
# "reference data" module -- named lists of code/label pairs (country
# codes, currency codes, region codes) -- but with genuine persistence
# and full CRUD.
#
# SEEDING: three lists ship pre-populated with real, verified public
# standards, sourced directly rather than from memory to avoid
# introducing subtly wrong codes into what's meant to be authoritative
# reference data:
#   - ISO 3166-1 alpha-2 country codes: fetched from datahub.io/core/
#     country-list (Public Domain Dedication and License), 249 entries.
#   - ISO 4217 currency codes: a curated set of currently-circulating
#     major world currencies (not the full ~180-entry list, which also
#     mixes in historic/fund codes) -- each code verified individually.
#   - Saudi Arabia's 13 administrative regions: ISO 3166-2:SA codes
#     (SA-01 through SA-14, SA-13 retired/unused, confirmed via ISO's
#     own subdivision registry and the UK FCDO's toponymic factfile).
# Everything else the reference platform ships as "reference data"
# (department codes, budget categories, record-status lifecycles) is
# org-specific with no real published values available -- those lists
# are deliberately NOT seeded here; a real one only gets created when
# someone supplies real values, never invented as a placeholder.
#
# SCOPE: same "org-level artifact" reasoning as glossary_adapter.py
# and policy_documents_adapter.py -- reference lists aren't scoped to
# a dataset, they're shared context every dataset can draw on.
#
# RBAC: same gate as Glossary/Classification/Stewardship
# (stewardship_assign_allow) for every mutation (list or value
# create/update/delete); reads are open, same unauthenticated-GET
# pattern as the rest of the dashboard.

from datetime import datetime, timezone

from app import db as db_module
from app.db import get_conn


def _ensure_schema():
    pk = "SERIAL PRIMARY KEY" if db_module._is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with get_conn() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS reference_lists (
                id {pk},
                name TEXT NOT NULL UNIQUE,
                slug TEXT NOT NULL UNIQUE,
                description TEXT,
                owner TEXT,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS reference_values (
                id {pk},
                list_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                label TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _list_row_to_dict(row, value_count: int) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "description": row["description"],
        "owner": row["owner"],
        "value_count": value_count,
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _value_row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "list_id": row["list_id"],
        "code": row["code"],
        "label": row["label"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_reference_lists() -> dict:
    """Every reference list with its value count -- the list view the
    MDM Reference Data page's table renders directly. No fabricated
    "sync status" -- this data isn't synced from anywhere external,
    it's entered and edited directly, so the honest fields are simply
    who owns it and when it last changed.

    Seeds the 3 standard lists on first call if they don't exist yet
    (idempotent -- see seed_standard_lists() below) rather than
    requiring a separate startup hook or manual trigger."""
    _ensure_schema()
    seed_standard_lists()
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM reference_lists ORDER BY LOWER(name)").fetchall()
        count_rows = conn.execute(
            "SELECT list_id, COUNT(*) AS c FROM reference_values GROUP BY list_id"
        ).fetchall()
    counts = {r["list_id"]: r["c"] for r in count_rows}
    lists = [_list_row_to_dict(r, counts.get(r["id"], 0)) for r in rows]
    return {"lists": lists, "lists_total": len(lists)}


def get_reference_list(list_id: int) -> dict:
    """One list with its full values, ordered by code -- powers the
    detail drawer."""
    _ensure_schema()
    with get_conn() as conn:
        list_row = conn.execute("SELECT * FROM reference_lists WHERE id = ?", (list_id,)).fetchone()
        if list_row is None:
            raise ValueError(f"No reference list {list_id} found.")
        value_rows = conn.execute(
            "SELECT * FROM reference_values WHERE list_id = ? ORDER BY LOWER(code)", (list_id,)
        ).fetchall()
    values = [_value_row_to_dict(r) for r in value_rows]
    result = _list_row_to_dict(list_row, len(values))
    result["values"] = values
    return result


def create_reference_list(payload: dict) -> dict:
    name = (payload.get("name") or "").strip()
    slug = (payload.get("slug") or "").strip().lower().replace(" ", "-")
    description = (payload.get("description") or "").strip() or None
    owner = (payload.get("owner") or "").strip() or None
    created_by = payload.get("created_by")

    if not name:
        raise ValueError("name is required.")
    if not slug:
        raise ValueError("slug is required.")
    if not created_by:
        raise ValueError("created_by is required.")

    _ensure_schema()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM reference_lists WHERE name = ? OR slug = ?", (name, slug)
        ).fetchone()
        if existing:
            raise ValueError(f"A reference list named '{name}' (or with slug '{slug}') already exists.")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO reference_lists
               (name, slug, description, owner, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, slug, description, owner, created_by, now, now),
        )
        conn.commit()

    return list_reference_lists()


def update_reference_list(payload: dict) -> dict:
    """Updates a list's description/owner in place -- name/slug are
    not renameable here, same "content edit only" posture
    glossary_adapter.update_term() already applies, since other lists
    or code may reference a list by its stable slug."""
    list_id = payload.get("id")
    description = (payload.get("description") or "").strip() or None
    owner = (payload.get("owner") or "").strip() or None

    if list_id is None:
        raise ValueError("id is required.")

    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM reference_lists WHERE id = ?", (list_id,)).fetchone()
        if existing is None:
            raise ValueError(f"No reference list {list_id} found.")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE reference_lists SET description = ?, owner = ?, updated_at = ? WHERE id = ?",
            (description, owner, now, list_id),
        )
        conn.commit()

    return list_reference_lists()


def delete_reference_list(payload: dict) -> dict:
    """Deletes a list and every value inside it. No native FK/CASCADE
    relied on here -- same reasoning as the rest of this codebase's
    SQLite/Postgres dual-backend adapters (see db.py's own docstring):
    an explicit child-delete-then-parent-delete works identically on
    both backends without depending on either one's constraint
    enforcement being configured the same way."""
    list_id = payload.get("id")
    if list_id is None:
        raise ValueError("id is required.")

    with get_conn() as conn:
        conn.execute("DELETE FROM reference_values WHERE list_id = ?", (list_id,))
        conn.execute("DELETE FROM reference_lists WHERE id = ?", (list_id,))
        conn.commit()

    return list_reference_lists()


def add_reference_value(payload: dict) -> dict:
    list_id = payload.get("list_id")
    code = (payload.get("code") or "").strip()
    label = (payload.get("label") or "").strip()

    if list_id is None:
        raise ValueError("list_id is required.")
    if not code:
        raise ValueError("code is required.")
    if not label:
        raise ValueError("label is required.")

    _ensure_schema()
    with get_conn() as conn:
        list_row = conn.execute("SELECT id FROM reference_lists WHERE id = ?", (list_id,)).fetchone()
        if list_row is None:
            raise ValueError(f"No reference list {list_id} found.")
        existing = conn.execute(
            "SELECT id FROM reference_values WHERE list_id = ? AND code = ?", (list_id, code)
        ).fetchone()
        if existing:
            raise ValueError(f"Code '{code}' already exists in this list.")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO reference_values (list_id, code, label, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (list_id, code, label, now, now),
        )
        conn.commit()

    return get_reference_list(list_id)


def update_reference_value(payload: dict) -> dict:
    value_id = payload.get("id")
    label = (payload.get("label") or "").strip()

    if value_id is None:
        raise ValueError("id is required.")
    if not label:
        raise ValueError("label is required.")

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM reference_values WHERE id = ?", (value_id,)).fetchone()
        if row is None:
            raise ValueError(f"No reference value {value_id} found.")
        list_id = row["list_id"]

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE reference_values SET label = ?, updated_at = ? WHERE id = ?",
            (label, now, value_id),
        )
        conn.commit()

    return get_reference_list(list_id)


def delete_reference_value(payload: dict) -> dict:
    value_id = payload.get("id")
    if value_id is None:
        raise ValueError("id is required.")

    with get_conn() as conn:
        row = conn.execute("SELECT list_id FROM reference_values WHERE id = ?", (value_id,)).fetchone()
        if row is None:
            raise ValueError(f"No reference value {value_id} found.")
        list_id = row["list_id"]
        conn.execute("DELETE FROM reference_values WHERE id = ?", (value_id,))
        conn.commit()

    return get_reference_list(list_id)


# ---------------------------------------------------------------------
# Standard-list seeding. Idempotent (check-then-insert, matching this
# codebase's established pattern for SQLite/Postgres compatibility --
# no ON CONFLICT/UPSERT used elsewhere in this file's sibling adapters
# either) -- safe to call on every app startup or re-run manually
# without duplicating rows. Only seeds a list if the slug doesn't
# already exist; never overwrites values someone has since edited.
# ---------------------------------------------------------------------

_SEED_CREATED_BY = "system (seeded standard)"

# ISO 3166-1 alpha-2 country codes -- fetched from datahub.io's
# public-domain country-list dataset (Name,Code CSV), 249 entries as
# published by the ISO 3166 Maintenance Agency.
_COUNTRY_CODES = [
    ("AF", "Afghanistan"), ("AL", "Albania"), ("DZ", "Algeria"), ("AS", "American Samoa"),
    ("AD", "Andorra"), ("AO", "Angola"), ("AI", "Anguilla"), ("AQ", "Antarctica"),
    ("AG", "Antigua and Barbuda"), ("AR", "Argentina"), ("AM", "Armenia"), ("AW", "Aruba"),
    ("AU", "Australia"), ("AT", "Austria"), ("AZ", "Azerbaijan"), ("BS", "Bahamas"),
    ("BH", "Bahrain"), ("BD", "Bangladesh"), ("BB", "Barbados"), ("BY", "Belarus"),
    ("BE", "Belgium"), ("BZ", "Belize"), ("BJ", "Benin"), ("BM", "Bermuda"),
    ("BT", "Bhutan"), ("BO", "Bolivia"), ("BQ", "Bonaire, Sint Eustatius and Saba"),
    ("BA", "Bosnia and Herzegovina"), ("BW", "Botswana"), ("BV", "Bouvet Island"),
    ("BR", "Brazil"), ("IO", "British Indian Ocean Territory"), ("BN", "Brunei Darussalam"),
    ("BG", "Bulgaria"), ("BF", "Burkina Faso"), ("BI", "Burundi"), ("CV", "Cabo Verde"),
    ("KH", "Cambodia"), ("CM", "Cameroon"), ("CA", "Canada"), ("KY", "Cayman Islands"),
    ("CF", "Central African Republic"), ("TD", "Chad"), ("CL", "Chile"), ("CN", "China"),
    ("CX", "Christmas Island"), ("CC", "Cocos (Keeling) Islands"), ("CO", "Colombia"),
    ("KM", "Comoros"), ("CD", "Congo (the Democratic Republic of the)"), ("CG", "Congo"),
    ("CK", "Cook Islands"), ("CR", "Costa Rica"), ("HR", "Croatia"), ("CU", "Cuba"),
    ("CW", "Curaçao"), ("CY", "Cyprus"), ("CZ", "Czechia"), ("CI", "Côte d'Ivoire"),
    ("DK", "Denmark"), ("DJ", "Djibouti"), ("DM", "Dominica"), ("DO", "Dominican Republic"),
    ("EC", "Ecuador"), ("EG", "Egypt"), ("SV", "El Salvador"), ("GQ", "Equatorial Guinea"),
    ("ER", "Eritrea"), ("EE", "Estonia"), ("SZ", "Eswatini"), ("ET", "Ethiopia"),
    ("FK", "Falkland Islands (Malvinas)"), ("FO", "Faroe Islands"), ("FJ", "Fiji"),
    ("FI", "Finland"), ("FR", "France"), ("GF", "French Guiana"), ("PF", "French Polynesia"),
    ("TF", "French Southern Territories"), ("GA", "Gabon"), ("GM", "Gambia"),
    ("GE", "Georgia"), ("DE", "Germany"), ("GH", "Ghana"), ("GI", "Gibraltar"),
    ("GR", "Greece"), ("GL", "Greenland"), ("GD", "Grenada"), ("GP", "Guadeloupe"),
    ("GU", "Guam"), ("GT", "Guatemala"), ("GG", "Guernsey"), ("GN", "Guinea"),
    ("GW", "Guinea-Bissau"), ("GY", "Guyana"), ("HT", "Haiti"),
    ("HM", "Heard Island and McDonald Islands"), ("VA", "Holy See"), ("HN", "Honduras"),
    ("HK", "Hong Kong"), ("HU", "Hungary"), ("IS", "Iceland"), ("IN", "India"),
    ("ID", "Indonesia"), ("IR", "Iran"), ("IQ", "Iraq"), ("IE", "Ireland"),
    ("IM", "Isle of Man"), ("IL", "Israel"), ("IT", "Italy"), ("JM", "Jamaica"),
    ("JP", "Japan"), ("JE", "Jersey"), ("JO", "Jordan"), ("KZ", "Kazakhstan"),
    ("KE", "Kenya"), ("KI", "Kiribati"), ("KP", "Korea (the Democratic People's Republic of)"),
    ("KR", "Korea (the Republic of)"), ("KW", "Kuwait"), ("KG", "Kyrgyzstan"),
    ("LA", "Lao People's Democratic Republic"), ("LV", "Latvia"), ("LB", "Lebanon"),
    ("LS", "Lesotho"), ("LR", "Liberia"), ("LY", "Libya"), ("LI", "Liechtenstein"),
    ("LT", "Lithuania"), ("LU", "Luxembourg"), ("MO", "Macao"), ("MG", "Madagascar"),
    ("MW", "Malawi"), ("MY", "Malaysia"), ("MV", "Maldives"), ("ML", "Mali"),
    ("MT", "Malta"), ("MH", "Marshall Islands"), ("MQ", "Martinique"), ("MR", "Mauritania"),
    ("MU", "Mauritius"), ("YT", "Mayotte"), ("MX", "Mexico"), ("FM", "Micronesia"),
    ("MD", "Moldova"), ("MC", "Monaco"), ("MN", "Mongolia"), ("ME", "Montenegro"),
    ("MS", "Montserrat"), ("MA", "Morocco"), ("MZ", "Mozambique"), ("MM", "Myanmar"),
    ("NA", "Namibia"), ("NR", "Nauru"), ("NP", "Nepal"), ("NL", "Netherlands"),
    ("NC", "New Caledonia"), ("NZ", "New Zealand"), ("NI", "Nicaragua"), ("NE", "Niger"),
    ("NG", "Nigeria"), ("NU", "Niue"), ("NF", "Norfolk Island"), ("MK", "North Macedonia"),
    ("MP", "Northern Mariana Islands"), ("NO", "Norway"), ("OM", "Oman"), ("PK", "Pakistan"),
    ("PW", "Palau"), ("PS", "Palestine, State of"), ("PA", "Panama"),
    ("PG", "Papua New Guinea"), ("PY", "Paraguay"), ("PE", "Peru"), ("PH", "Philippines"),
    ("PN", "Pitcairn"), ("PL", "Poland"), ("PT", "Portugal"), ("PR", "Puerto Rico"),
    ("QA", "Qatar"), ("RO", "Romania"), ("RU", "Russian Federation"), ("RW", "Rwanda"),
    ("RE", "Réunion"), ("BL", "Saint Barthélemy"),
    ("SH", "Saint Helena, Ascension and Tristan da Cunha"), ("KN", "Saint Kitts and Nevis"),
    ("LC", "Saint Lucia"), ("MF", "Saint Martin (French part)"),
    ("PM", "Saint Pierre and Miquelon"), ("VC", "Saint Vincent and the Grenadines"),
    ("WS", "Samoa"), ("SM", "San Marino"), ("ST", "Sao Tome and Principe"),
    ("SA", "Saudi Arabia"), ("SN", "Senegal"), ("RS", "Serbia"), ("SC", "Seychelles"),
    ("SL", "Sierra Leone"), ("SG", "Singapore"), ("SX", "Sint Maarten (Dutch part)"),
    ("SK", "Slovakia"), ("SI", "Slovenia"), ("SB", "Solomon Islands"), ("SO", "Somalia"),
    ("ZA", "South Africa"), ("GS", "South Georgia and the South Sandwich Islands"),
    ("SS", "South Sudan"), ("ES", "Spain"), ("LK", "Sri Lanka"), ("SD", "Sudan"),
    ("SR", "Suriname"), ("SJ", "Svalbard and Jan Mayen"), ("SE", "Sweden"),
    ("CH", "Switzerland"), ("SY", "Syrian Arab Republic"), ("TW", "Taiwan"),
    ("TJ", "Tajikistan"), ("TZ", "Tanzania, the United Republic of"), ("TH", "Thailand"),
    ("TL", "Timor-Leste"), ("TG", "Togo"), ("TK", "Tokelau"), ("TO", "Tonga"),
    ("TT", "Trinidad and Tobago"), ("TN", "Tunisia"), ("TM", "Turkmenistan"),
    ("TC", "Turks and Caicos Islands"), ("TV", "Tuvalu"), ("TR", "Türkiye"),
    ("UG", "Uganda"), ("UA", "Ukraine"), ("AE", "United Arab Emirates"),
    ("GB", "United Kingdom of Great Britain and Northern Ireland"),
    ("UM", "United States Minor Outlying Islands"), ("US", "United States of America"),
    ("UY", "Uruguay"), ("UZ", "Uzbekistan"), ("VU", "Vanuatu"), ("VE", "Venezuela"),
    ("VN", "Viet Nam"), ("VG", "Virgin Islands (British)"), ("VI", "Virgin Islands (U.S.)"),
    ("WF", "Wallis and Futuna"), ("EH", "Western Sahara"), ("YE", "Yemen"),
    ("ZM", "Zambia"), ("ZW", "Zimbabwe"), ("AX", "Åland Islands"),
]

# ISO 4217 currency codes -- a curated set of currently-circulating
# major currencies (each code individually verified), not the full
# ISO list which also mixes in historic/fund codes.
_CURRENCY_CODES = [
    ("USD", "US Dollar"), ("EUR", "Euro"), ("GBP", "Pound Sterling"),
    ("JPY", "Yen"), ("CHF", "Swiss Franc"), ("CAD", "Canadian Dollar"),
    ("AUD", "Australian Dollar"), ("NZD", "New Zealand Dollar"), ("CNY", "Yuan Renminbi"),
    ("HKD", "Hong Kong Dollar"), ("SGD", "Singapore Dollar"), ("INR", "Indian Rupee"),
    ("KRW", "Won"), ("SAR", "Saudi Riyal"), ("AED", "UAE Dirham"), ("QAR", "Qatari Rial"),
    ("KWD", "Kuwaiti Dinar"), ("BHD", "Bahraini Dinar"), ("OMR", "Rial Omani"),
    ("JOD", "Jordanian Dinar"), ("EGP", "Egyptian Pound"), ("TRY", "Turkish Lira"),
    ("ZAR", "Rand"), ("RUB", "Russian Ruble"), ("BRL", "Brazilian Real"),
    ("MXN", "Mexican Peso"), ("SEK", "Swedish Krona"), ("NOK", "Norwegian Krone"),
    ("DKK", "Danish Krone"), ("PLN", "Zloty"), ("THB", "Baht"), ("MYR", "Malaysian Ringgit"),
    ("IDR", "Rupiah"), ("PHP", "Philippine Peso"), ("PKR", "Pakistan Rupee"),
    ("VND", "Dong"), ("ILS", "New Israeli Sheqel"),
]

# Saudi Arabia's 13 administrative regions -- ISO 3166-2:SA codes
# (SA-01 through SA-14; SA-13 is retired/unassigned, consistent with
# ISO's own "01-14 except 13" numbering for this entry).
_SAUDI_REGIONS = [
    ("SA-01", "Riyadh"), ("SA-02", "Makkah al Mukarramah"),
    ("SA-03", "Al Madinah al Munawwarah"), ("SA-04", "Eastern Province"),
    ("SA-05", "Al Qassim"), ("SA-06", "Ha'il"), ("SA-07", "Tabuk"),
    ("SA-08", "Northern Borders"), ("SA-09", "Jazan"), ("SA-10", "Najran"),
    ("SA-11", "Al Bahah"), ("SA-12", "Al Jawf"), ("SA-14", "Asir"),
]

_STANDARD_LISTS = [
    {
        "slug": "iso-country-codes",
        "name": "ISO Country Codes",
        "description": "ISO 3166-1 alpha-2 country codes and official short names.",
        "values": _COUNTRY_CODES,
    },
    {
        "slug": "iso-currency-codes",
        "name": "ISO Currency Codes",
        "description": "ISO 4217 codes for currently-circulating major currencies.",
        "values": _CURRENCY_CODES,
    },
    {
        "slug": "saudi-administrative-regions",
        "name": "Saudi Arabia Administrative Regions",
        "description": "The Kingdom's 13 administrative regions (ISO 3166-2:SA codes).",
        "values": _SAUDI_REGIONS,
    },
]


def seed_standard_lists() -> dict:
    """Idempotent: only creates a standard list if its slug doesn't
    already exist, and never touches an existing one's values --
    someone may have since edited or added to it. Safe to call
    repeatedly (e.g. once per app startup).

    REAL BUG FOUND AND FIXED (2026-08-20, live testing): this
    originally issued one INSERT per value -- up to 249 individual
    round trips for the country list alone, 298 total across all 3
    seed lists. Against the real (cross-region) Postgres connection
    that took ~39s and ultimately failed with an unhandled error --
    confirmed live: GET /api/mdm/reference-data hung ~39s then
    returned a bare 500. Reproduced instantly and successfully against
    local SQLite, which is what pointed at the round-trip count
    specifically rather than the seed data or query logic. Now a
    single multi-row INSERT per list (3 round trips total instead of
    298) -- same idempotency, same data, no behavior change other than
    speed. The masking half of this bug -- the read endpoint that
    triggers seeding had no try/except ValueError wrapper, unlike
    every write route elsewhere in this codebase, so whatever the
    underlying Postgres error actually was got swallowed into an
    opaque 500 instead of surfacing a real message -- is fixed
    separately in main.py."""
    _ensure_schema()
    created = []
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        for spec in _STANDARD_LISTS:
            existing = conn.execute(
                "SELECT id FROM reference_lists WHERE slug = ?", (spec["slug"],)
            ).fetchone()
            if existing:
                continue
            conn.execute(
                """INSERT INTO reference_lists
                   (name, slug, description, owner, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (spec["name"], spec["slug"], spec["description"], None, _SEED_CREATED_BY, now, now),
            )
            list_id = conn.execute(
                "SELECT id FROM reference_lists WHERE slug = ?", (spec["slug"],)
            ).fetchone()["id"]

            values = spec["values"]
            placeholders = ", ".join(["(?, ?, ?, ?, ?)"] * len(values))
            params = []
            for code, label in values:
                params.extend([list_id, code, label, now, now])
            conn.execute(
                f"""INSERT INTO reference_values (list_id, code, label, created_at, updated_at)
                    VALUES {placeholders}""",
                tuple(params),
            )
            created.append(spec["slug"])
        conn.commit()
    return {"seeded": created}
