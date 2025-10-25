#!/usr/bin/env python3
"""
Update geolocation data using OpenCage API.

This script updates *_profiles.db databases by adding geolocation
(country, state, city, latitude, longitude, full_address) to the
qualification table for institutions.

API keys are loaded securely from environment variables.
"""

import os
import time
import logging
import sqlite3
import requests
from typing import List, Optional
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

# ------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------
load_dotenv()

DB_DIR = os.path.join(os.path.dirname(__file__), "databases")
CENTRAL_DB = os.path.join(DB_DIR, "institute_urls.db")

REQUEST_DELAY = 1.0  # seconds between API calls
MAX_RETRIES = 3
OPENCAGE_URL = "https://api.opencagedata.com/geocode/v1/json"

# ------------------------------------------------------------
# API KEYS
# ------------------------------------------------------------
def load_api_keys() -> List[str]:
    env_keys = os.getenv("OPENCAGE_KEYS")
    if env_keys:
        return [k.strip() for k in env_keys.split(",") if k.strip()]
    keys_file = os.path.join(DB_DIR, "opencage_keys.txt")
    if os.path.exists(keys_file):
        with open(keys_file, "r") as f:
            return [line.strip() for line in f if line.strip()]
    raise RuntimeError("No OpenCage API keys found. Set OPENCAGE_KEYS or create opencage_keys.txt.")

API_KEYS = load_api_keys()

# ------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("geocode_updater")

# ------------------------------------------------------------
# DATABASE UTILITIES
# ------------------------------------------------------------
def safe_filename(name: str) -> str:
    """Make a safe filename from an institution name."""
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in name)

def create_connection(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def fetch_institutes_by_type(org_type: str) -> List[str]:
    """Return a list of institutes of a given type with non-empty URLs."""
    with sqlite3.connect(CENTRAL_DB) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT institute_name FROM institutes WHERE org_type = ? AND url IS NOT NULL",
            (org_type,),
        )
        return [r[0] for r in cur.fetchall()]

def ensure_geo_columns(conn: sqlite3.Connection):
    """Ensure that qualification table has required geolocation columns."""
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(qualification)")
    existing = [r[1] for r in cur.fetchall()]
    columns = [
        ("country", "TEXT"),
        ("state", "TEXT"),
        ("city", "TEXT"),
        ("latitude", "REAL"),
        ("longitude", "REAL"),
        ("full_address", "TEXT"),
    ]
    for col, typ in columns:
        if col not in existing:
            cur.execute(f"ALTER TABLE qualification ADD COLUMN {col} {typ}")
    conn.commit()

def rotate_key(current_index: int) -> int:
    """Rotate to the next API key index, waiting if all keys are exhausted."""
    next_index = (current_index + 1) % len(API_KEYS)
    if next_index == 0:
        logger.warning("All API keys may be exhausted. Waiting 1 hour before retrying.")
        time.sleep(3600)
    logger.info("Rotating to next API key (index %d)", next_index)
    return next_index

# ------------------------------------------------------------
# OPENCAGE API LOGIC
# ------------------------------------------------------------
def geocode_institution(name: str, api_key: str) -> Optional[dict]:
    """Query OpenCage API and return geolocation information."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            params = {"q": name, "key": api_key, "limit": 1}
            r = requests.get(OPENCAGE_URL, params=params, timeout=10)
            if r.status_code == 402:  # quota exceeded
                return {"quota_exceeded": True}
            r.raise_for_status()
            data = r.json()
            if data.get("results"):
                res = data["results"][0]
                components = res.get("components") or {}
                geometry = res.get("geometry") or {}
                city = components.get("city") or components.get("town") or components.get("village")
                return {
                    "country": components.get("country"),
                    "state": components.get("state"),
                    "city": city,
                    "latitude": geometry.get("lat"),
                    "longitude": geometry.get("lng"),
                    "full_address": res.get("formatted"),
                }
            # Return empty geo data if no results
            return {k: None for k in ["country","state","city","latitude","longitude","full_address"]}
        except requests.RequestException as e:
            logger.warning("Retry %d for %s: %s", attempt, name, e)
            time.sleep(2)
    return {k: None for k in ["country","state","city","latitude","longitude","full_address"]}

# ------------------------------------------------------------
# UPDATE DATABASE
# ------------------------------------------------------------
def update_institute_db(institute_name: str):
    """Update geolocation data for a single institute database."""
    db_path = os.path.join(DB_DIR, f"{safe_filename(institute_name)}_profiles.db")
    if not os.path.exists(db_path):
        logger.warning("Database not found for %s", institute_name)
        return

    logger.info("Updating geolocation for %s", db_path)
    with create_connection(db_path) as conn:
        ensure_geo_columns(conn)
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT institution FROM qualification WHERE institution IS NOT NULL AND TRIM(institution) != ''"
        )
        institutions = [r[0] for r in cur.fetchall()]

        key_idx = 0
        updated_count = 0

        for inst in institutions:
            try:
                geo = geocode_institution(inst.strip(), API_KEYS[key_idx])
                if geo == {"quota_exceeded": True}:
                    key_idx = rotate_key(key_idx)
                    geo = geocode_institution(inst.strip(), API_KEYS[key_idx])

                for k in ["country","state","city","latitude","longitude","full_address"]:
                    geo.setdefault(k, None)

                cur.execute(
                    """
                    UPDATE qualification
                    SET country=?, state=?, city=?, latitude=?, longitude=?, full_address=?
                    WHERE institution=?;
                    """,
                    (
                        geo["country"],
                        geo["state"],
                        geo["city"],
                        geo["latitude"],
                        geo["longitude"],
                        geo["full_address"],
                        inst,
                    ),
                )
                conn.commit()
                updated_count += 1
                logger.info("Updated %s (%s)", inst, institute_name)
            except Exception as e:
                logger.error("Error updating %s: %s", inst, e)
            time.sleep(REQUEST_DELAY)

        logger.info("Completed %s — %d institutions updated.", institute_name, updated_count)

# ------------------------------------------------------------
# MULTITHREADING
# ------------------------------------------------------------
def update_all_institutes(org_type: str):
    """Update geolocation data for all institutes of a given type."""
    institutes = fetch_institutes_by_type(org_type)
    logger.info("Found %d institutes of type '%s'", len(institutes), org_type)

    if not institutes:
        return

    n_threads = len(API_KEYS)
    with ThreadPoolExecutor(max_workers=n_threads) as executor:
        futures = {executor.submit(update_institute_db, name): name for name in institutes}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error("Error updating %s: %s", futures[future], e)

# ------------------------------------------------------------
# ENTRY POINT
# ------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Update qualification geolocation using OpenCage API.")
    parser.add_argument("--org-type", required=True, help="Organization type to process (e.g., 'University')")
    args = parser.parse_args()
    update_all_institutes(args.org_type)

if __name__ == "__main__":
    main()
