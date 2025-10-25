#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build Researcher Career Flow Database
-------------------------------------
Filters institutes by organization type and aggregates researcher career data.

"""

from __future__ import annotations
import os
import sqlite3
import pandas as pd
import logging
import argparse

# ---------------- Configuration ----------------
BASE_DIR = "databases"
MAIN_DB = os.path.join(BASE_DIR, "irins_institute_urls.db")

# ---------------- Logging Setup ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("CareerFlowBuilder")

# ---------------- Helper Functions ----------------
def safe_filename(name: str) -> str:
    """Return a filesystem-safe version of a string."""
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in name)

def normalize_inst_name(name: str) -> str:
    """Normalize institute name for consistent lookup."""
    if not name:
        return ""
    name = name.lower().strip()
    for ch in ",.-":
        name = name.replace(ch, "")
    return " ".join(name.split())

def load_institute_coordinates(db_path: str, org_type: str | None = None) -> dict[str, tuple[float, float]]:
    """
    Load institute name → (latitude, longitude) mapping.
    Optionally filter by organization type.
    """
    logger.info("Loading institute coordinates from %s", db_path)
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT institute_name, latitude, longitude, org_type FROM institutes", conn)
    conn.close()

    if org_type:
        df = df[df["org_type"] == org_type]

    # Convert lat/lon to numeric, ignoring errors
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

    coord_map = {
        normalize_inst_name(str(row["institute_name"])): (row["latitude"], row["longitude"])
        for _, row in df.iterrows()
        if pd.notna(row["latitude"]) and pd.notna(row["longitude"])
    }

    logger.info("Loaded %d institutes with valid coordinates (org_type=%s).", len(coord_map), org_type)
    return coord_map

def extract_researcher_flow(db_path: str, coord_map: dict[str, tuple[float, float]]) -> list[dict]:
    """
    Extract researcher career flow data from a profile database.
    Returns a list of researcher records.
    """
    results = []
    conn = sqlite3.connect(db_path)

    try:
        info = pd.read_sql_query("SELECT * FROM personal_info", conn)
        qual = pd.read_sql_query("SELECT * FROM qualification", conn)
        expertise = pd.read_sql_query("SELECT * FROM expertise", conn)
        metrics = pd.read_sql_query("SELECT * FROM metrics", conn)
        pubs = pd.read_sql_query("SELECT * FROM publications", conn)
        details = pd.read_sql_query("SELECT * FROM personal_details", conn)
        exp = pd.read_sql_query("SELECT * FROM experience", conn)
    except Exception as e:
        logger.warning("Skipping %s due to read error: %s", db_path, e)
        conn.close()
        return results
    conn.close()

    if info.empty or qual.empty:
        return results

    for _, row in info.iterrows():
        vidwan_id = row["vidwan_id"]
        name = row["name"]
        designation = row["designation"]
        current_inst = str(row["institution"]).strip() if row.get("institution") else None
        experience_years = row.get("experience_years")

        # --- Ph.D. Institution ---
        my_qual = qual[qual["vidwan_id"] == vidwan_id]
        phd_rows = my_qual[my_qual["degree"].str.contains(r"Ph\.?D|Doctor of Philosophy", case=False, na=False, regex=True)]
        if phd_rows.empty:
            continue
        phd_row = phd_rows.iloc[0]
        phd_inst = str(phd_row.get("institution")).strip() if phd_row.get("institution") else None
        phd_lat = phd_row.get("latitude")
        phd_lon = phd_row.get("longitude")
        if (pd.isna(phd_lat) or pd.isna(phd_lon)) and phd_inst:
            phd_lat, phd_lon = coord_map.get(normalize_inst_name(phd_inst), (None, None))

        phd_country = phd_row.get("country")
        phd_state = phd_row.get("state")
        phd_city = phd_row.get("city")

        # --- Expertise ---
        my_exp = expertise[expertise["vidwan_id"] == vidwan_id]
        field = my_exp["main_expertise"].iloc[0] if not my_exp.empty else None

        # --- Metrics ---
        metric_row = metrics[metrics["vidwan_id"] == vidwan_id]
        total_citations = int(metric_row["total_citations"].iloc[0]) if not metric_row.empty else None
        h_index = int(metric_row["h_index"].iloc[0]) if not metric_row.empty else None
        if h_index == 0:
            h_index = None

        # --- Publications ---
        pub_row = pubs[pubs["vidwan_id"] == vidwan_id]
        num_publications = None
        if not pub_row.empty:
            j = int(pub_row["journal_articles"].iloc[0] or 0)
            c = int(pub_row["conference_proceedings"].iloc[0] or 0)
            r = int(pub_row["reviews"].iloc[0] or 0)
            o = int(pub_row["others"].iloc[0] or 0)
            num_publications = j + c + r + o

        publications_per_year = round(num_publications / experience_years, 2) if num_publications and experience_years else None

        # --- Personal details ---
        details_row = details[details["vidwan_id"] == vidwan_id]
        gender = details_row["gender"].iloc[0] if not details_row.empty else None
        address = details_row["country"].iloc[0] if not details_row.empty else None

        # --- Current institution coordinates ---
        curr_lat, curr_lon = coord_map.get(normalize_inst_name(current_inst), (None, None)) if current_inst else (None, None)

        # --- Previous experience ---
        previous_title = previous_institution = None
        exp_rows = exp[exp["vidwan_id"] == vidwan_id]
        if not exp_rows.empty:
            exp_rows_unique = exp_rows.drop_duplicates(subset=["title", "department", "institution", "start_year", "end_year"])
            prev_rows = exp_rows_unique[exp_rows_unique["end_year"].notna() & (exp_rows_unique["end_year"].str.lower() != "present")] \
                if "end_year" in exp_rows_unique else pd.DataFrame()
            if not prev_rows.empty:
                latest_prev = prev_rows.sort_values(by=["end_year"], ascending=False).iloc[0]
                previous_title = latest_prev.get("title")
                previous_institution = latest_prev.get("institution")

        # --- Build final record ---
        results.append({
            "vidwan_id": vidwan_id,
            "name": name,
            "designation": designation,
            "gender": gender,
            "address": address,
            "field": field,
            "experience_years": experience_years,
            "phd_institution": phd_inst,
            "phd_country": phd_country,
            "phd_state": phd_state,
            "phd_city": phd_city,
            "phd_lat": phd_lat,
            "phd_lon": phd_lon,
            "current_institution": current_inst,
            "previous_title": previous_title,
            "previous_institution": previous_institution,
            "curr_lat": curr_lat,
            "curr_lon": curr_lon,
            "total_citations": total_citations,
            "h_index": h_index,
            "num_publications": num_publications,
            "publications_per_year": publications_per_year
        })

    return results

# ---------------- Save Records to SQLite ----------------
def save_to_database(records: list[dict], db_path: str) -> None:
    """Save researcher records to SQLite database, ensuring uniqueness."""
    if not records:
        logger.warning("No valid researcher flow data to save.")
        return

    seen_ids = set()
    unique_records = [r for r in records if r["vidwan_id"] not in seen_ids and not seen_ids.add(r["vidwan_id"])]

    logger.info("Saving %d unique researcher records to %s", len(unique_records), db_path)
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS career_flows")
        cur.execute("""
            CREATE TABLE career_flows (
                vidwan_id TEXT PRIMARY KEY,
                name TEXT,
                designation TEXT,
                gender TEXT,
                address TEXT,
                field TEXT,
                experience_years INTEGER,
                phd_institution TEXT,
                phd_country TEXT,
                phd_state TEXT,
                phd_city TEXT,
                phd_lat REAL,
                phd_lon REAL,
                current_institution TEXT,
                previous_title TEXT,
                previous_institution TEXT,
                curr_lat REAL,
                curr_lon REAL,
                total_citations INTEGER,
                h_index INTEGER,
                num_publications INTEGER,
                publications_per_year REAL
            )
        """)

        insert_q = """
            INSERT INTO career_flows (
                vidwan_id, name, designation, gender, address, field, experience_years,
                phd_institution, phd_country, phd_state, phd_city,
                phd_lat, phd_lon, current_institution,
                previous_title, previous_institution,
                curr_lat, curr_lon, total_citations, h_index, num_publications, publications_per_year
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        for r in unique_records:
            cur.execute(insert_q, tuple(r.values()))
        conn.commit()

    logger.info("Database successfully saved at %s", db_path)

# ---------------- Main CLI Entry Point ----------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Build researcher flows DB filtered by org_type.")
    parser.add_argument("--org-type", required=True, help="Organization type to filter institutes (e.g., NITs, Universities)")
    args = parser.parse_args()

    org_type = args.org_type.strip()
    output_db = os.path.join(BASE_DIR, f"researcher_flows_{safe_filename(org_type)}.db")

    logger.info("Starting researcher flow database build for org_type=%s...", org_type)

    # Load institute coordinates
    coord_map = load_institute_coordinates(MAIN_DB, org_type)
    all_records = []

    # Fetch institutes of this org_type
    with sqlite3.connect(MAIN_DB) as conn:
        cur = conn.cursor()
        cur.execute("SELECT institute_name FROM institutes WHERE org_type = ?", (org_type,))
        institute_names = [row[0] for row in cur.fetchall()]
    logger.info("Found %d institutes with org_type=%s.", len(institute_names), org_type)

    # Extract researcher data for each institute
    for inst_name in institute_names:
        safe_name = safe_filename(inst_name)
        db_path = os.path.join(BASE_DIR, f"{safe_name}_profiles.db")
        if not os.path.exists(db_path):
            logger.warning("Profiles DB not found for %s → %s", inst_name, db_path)
            continue
        recs = extract_researcher_flow(db_path, coord_map)
        all_records.extend(recs)
        logger.info("Processed %s → %d valid researchers", db_path, len(recs))

    logger.info("Total aggregated researchers: %d", len(all_records))
    save_to_database(all_records, output_db)
    logger.info("Researcher flow database build completed successfully.")

if __name__ == "__main__":
    main()
