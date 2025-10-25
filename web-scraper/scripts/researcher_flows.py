#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build Researcher Career Flow Database

Aggregates data from multiple *_profiles.db files (one per institute) and
combines them with IRINS institute coordinates to create a single SQLite
database.
Output:
    databases/researcher_flows.db

"""

from __future__ import annotations
import os
import sqlite3
import pandas as pd
import logging

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------
BASE_DIR = "databases"
MAIN_DB = os.path.join(BASE_DIR, "irins_institute_urls.db")
OUTPUT_DB = os.path.join(BASE_DIR, "researcher_flows.db")

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("CareerFlowBuilder")

# -------------------------------------------------------------------
# Helper functions
# -------------------------------------------------------------------
def safe_filename(name: str) -> str:
    """Return a filesystem-safe version of a name."""
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in name)

def normalize_inst_name(name: str) -> str:
    """Normalize institute name for consistent lookup."""
    if not name:
        return ""
    name = name.lower().strip()
    for ch in ",.-":
        name = name.replace(ch, "")
    return " ".join(name.split())

def load_institute_coordinates(db_path: str) -> dict[str, tuple[float, float]]:
    """Load institute name → (lat, lon) mapping."""
    logger.info("Loading institute coordinates from %s", db_path)
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT institute_name, latitude, longitude FROM institutes", conn)
    conn.close()

    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

    coord_map = {
        normalize_inst_name(str(row["institute_name"])): (row["latitude"], row["longitude"])
        for _, row in df.iterrows()
        if pd.notna(row["latitude"]) and pd.notna(row["longitude"])
    }

    logger.info("Loaded %d institutes with valid coordinates.", len(coord_map))
    return coord_map

def extract_researcher_flow(db_path: str, coord_map: dict[str, tuple[float, float]]) -> list[dict]:
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
        current_inst = str(row["institution"]).strip() if row["institution"] else None
        experience_years = row.get("experience_years") if "experience_years" in row else None

        # --- Ph.D. institution ---
        my_qual = qual[qual["vidwan_id"] == vidwan_id]
        phd_rows = my_qual[my_qual["degree"].str.contains(r"Ph\.?D|Doctor of Philosophy", case=False, na=False, regex=True)]
        if phd_rows.empty:
            continue
        phd_row = phd_rows.iloc[0]
        phd_inst = str(phd_row["institution"]).strip() if phd_row["institution"] else None
        phd_lat = phd_row.get("latitude") if not pd.isna(phd_row.get("latitude")) else None
        phd_lon = phd_row.get("longitude") if not pd.isna(phd_row.get("longitude")) else None
        if (phd_lat is None or phd_lon is None) and phd_inst:
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

        # --- Publications per year ---
        publications_per_year = None
        if num_publications and experience_years and experience_years > 0:
            publications_per_year = round(num_publications / experience_years, 2)

        # --- Personal details ---
        details_row = details[details["vidwan_id"] == vidwan_id]
        gender = details_row["gender"].iloc[0] if not details_row.empty else None
        address = details_row["country"].iloc[0] if not details_row.empty else None

        # --- Coordinates for current institution ---
        curr_lat, curr_lon = coord_map.get(normalize_inst_name(current_inst), (None, None)) if current_inst else (None, None)

        # --- Previous Experience (end_year not 'Present') ---
        previous_title = previous_institution = None
        exp_rows = exp[exp["vidwan_id"] == vidwan_id]
        if not exp_rows.empty:
            exp_rows_unique = exp_rows.drop_duplicates(subset=["title", "department", "institution", "start_year", "end_year"])
            prev_rows = exp_rows_unique[
                exp_rows_unique["end_year"].notna() &
                (exp_rows_unique["end_year"].str.lower() != "present")
            ] if "end_year" in exp_rows_unique else pd.DataFrame()

            if not prev_rows.empty:
                # Try to pick most recent past role
                prev_rows = prev_rows.sort_values(by=["end_year"], ascending=False, na_position="last")
                latest_prev = prev_rows.iloc[0]
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


# -------------------------------------------------------------------
# Save to database
# -------------------------------------------------------------------
def save_to_database(records: list[dict], db_path: str) -> None:
    if not records:
        logger.warning("No valid researcher flow data to save.")
        return

    seen_ids = set()
    unique_records = []
    for r in records:
        if r["vidwan_id"] not in seen_ids:
            unique_records.append(r)
            seen_ids.add(r["vidwan_id"])

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
            cur.execute(insert_q, (
                r["vidwan_id"], r["name"], r["designation"], r["gender"], r["address"], r["field"],
                r["experience_years"], r["phd_institution"], r["phd_country"], r["phd_state"], r["phd_city"],
                r["phd_lat"], r["phd_lon"], r["current_institution"],
                r["previous_title"], r["previous_institution"],
                r["curr_lat"], r["curr_lon"], r["total_citations"], r["h_index"],
                r["num_publications"], r["publications_per_year"]
            ))

        conn.commit()

    logger.info("Database successfully saved at %s", db_path)

# -------------------------------------------------------------------
# Main function (all institutes, no org_type filtering)
# -------------------------------------------------------------------
def main() -> None:
    logger.info("Starting researcher flow database build for all institutes...")

    # Load institute coordinates
    coord_map = load_institute_coordinates(MAIN_DB)
    all_records = []

    # Get all institutes
    with sqlite3.connect(MAIN_DB) as conn:
        cur = conn.cursor()
        cur.execute("SELECT institute_name FROM institutes")
        institute_names = [row[0] for row in cur.fetchall()]
    logger.info("Found %d institutes in total.", len(institute_names))

    # Process all institutes
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
    save_to_database(all_records, OUTPUT_DB)
    logger.info("Researcher flow database build completed successfully.")


if __name__ == "__main__":
    main()
