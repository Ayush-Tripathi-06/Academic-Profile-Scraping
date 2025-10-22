#!/usr/bin/env python3
"""
Update Qualification Location Data using OpenCage API

This script updates *_profiles.db databases by adding geolocation data
(country, state, city, latitude, longitude, and full address)
to the qualification table.

It reads institute list from irins_institute_urls.db filtered by org_type,
and geocodes using OpenCage API keys loaded securely from environment
variables or a local text file (never committed).

Author: Ayush Tripathi
Date: 2025-10-22
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

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_DIR = os.path.join(REPO_ROOT, "databases")
CENTRAL_DB = os.path.join(DB_DIR, "irins_institute_urls.db")

# ---- Load OpenCage API Keys Safely ----
def load_api_keys() -> List[str]:
    env_keys = os.getenv("OPENCAGE_KEYS")
    if env_keys:
        return [k.strip() for k in env_keys.split(",") if k.strip()]
    keys_file = os.path.join(REPO_ROOT, "opencage_keys.txt")
    if os.path.exists(keys_file):
        with open(keys_file, "r") as f:
            return [line.strip() for line in f if line.strip()]
    raise RuntimeError(
        "No API keys found. Set OPENCAGE_KEYS in .env or create opencage_keys.txt (one key per line)."
    )

API_KEYS = load_api_keys()
OPENCAGE_URL = "https://api.opencagedata.com/geocode/v1/json"
REQUEST_DELAY = 1.0
MAX_RETRIES = 3

# ---- Logging ----
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("opencage_updater")

# ------------------------------------------------------------
# DATABASE UTILITIES
# ------------------------------------------------------------
def safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in name)

def create_connection(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def fetch_institutes_by_type(org_type: str) -> List[str]:
    with sqlite3.connect(CENTRAL_DB) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT institute_name FROM institutes WHERE org_type = ? AND irins_url IS NOT NULL",
            (org_type,),
        )
        return [r[0] for r in cur.fetchall()]

def ensure_geo_columns(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(qualification)")
    existing = [r[1] for r in cur.fetchall()]
    new_cols = [
        ("country", "TEXT"),
        ("state", "TEXT"),
        ("city", "TEXT"),
        ("latitude", "REAL"),
        ("longitude", "REAL"),
        ("full_address", "TEXT"),
    ]
    for col, typ in new_cols:
        if col not in existing:
            cur.execute(f"ALTER TABLE qualification ADD COLUMN {col} {typ}")
    conn.commit()

# ------------------------------------------------------------
# OPENCAGE API LOGIC
# ------------------------------------------------------------
def geocode_institution(name: str, api_key: str) -> Optional[dict]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            params = {"q": name, "key": api_key, "limit": 1}
            r = requests.get(OPENCAGE_URL, params=params, timeout=10)
            if r.status_code == 402:  # quota exceeded
                return {"quota_exceeded": True}
            r.raise_for_status()
            data = r.json()
            if data["results"]:
                res = data["results"][0]
                components = res["components"]
                geometry = res["geometry"]
                return {
                    "country": components.get("country"),
                    "state": components.get("state"),
                    "city": components.get("city") or components.get("town") or components.get("village"),
                    "latitude": geometry.get("lat"),
                    "longitude": geometry.get("lng"),
                    "full_address": res.get("formatted"),
                }
            return None
        except requests.RequestException as e:
            logger.warning("Retry %d for %s: %s", attempt, name, e)
            time.sleep(2)
    return None

def rotate_key(idx: int) -> int:
    return (idx + 1) % len(API_KEYS)

# ------------------------------------------------------------
# SINGLE INSTITUTE DB UPDATE (sequentially)
# ------------------------------------------------------------
def update_institute_db(institute_name: str):
    safe_name = safe_filename(institute_name)
    db_path = os.path.join(DB_DIR, f"{safe_name}_profiles.db")
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
        updated = 0

        for inst in institutions:
            geo = geocode_institution(inst, API_KEYS[key_idx])
            if geo == {"quota_exceeded": True}:
                key_idx = rotate_key(key_idx)
                geo = geocode_institution(inst, API_KEYS[key_idx])

            if geo:
                cur.execute(
                    """
                    UPDATE qualification
                    SET country=?, state=?, city=?, latitude=?, longitude=?, full_address=?
                    WHERE institution=?;
                    """,
                    (
                        geo["country"], geo["state"], geo["city"],
                        geo["latitude"], geo["longitude"], geo["full_address"], inst,
                    ),
                )
                conn.commit()
                updated += 1
                logger.info("Updated %s (%s)", inst, institute_name)
            else:
                logger.info("No result for %s", inst)

            time.sleep(REQUEST_DELAY)

        logger.info("Completed %s — %d institutions updated.", institute_name, updated)

# ------------------------------------------------------------
# MULTITHREADING AT *_profiles.db LEVEL
# ------------------------------------------------------------
def update_all_institutes(org_type: str):
    institutes = fetch_institutes_by_type(org_type)
    logger.info("Found %d institutes of type '%s'", len(institutes), org_type)

    if not institutes:
        logger.info("No institutes to update.")
        return

    # ---- Define number of threads ----
    n_threads = min(len(API_KEYS), 8)

    with ThreadPoolExecutor(max_workers=n_threads) as executor:
        futures = {executor.submit(update_institute_db, name): name for name in institutes}

        for future in as_completed(futures):
            name = futures[future]
            try:
                future.result()
            except Exception as e:
                logger.error("Error updating %s: %s", name, e)


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
