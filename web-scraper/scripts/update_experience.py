#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Update faculty experience across all profile databases.

Features:
  - Deduplicate identical experience entries.
  - Compute total experience per faculty.
  - Replace zero-year experiences with NULL.
  - Safe, logged, modular workflow.

"""

import os
import sqlite3
import logging
from datetime import datetime
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_DIR = "./databases"
DATABASE_SUFFIX = "_profiles.db"
LOG_FILE = "update_experience.log"
CURRENT_YEAR = datetime.now().year

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------

def find_databases(directory: str, suffix: str) -> List[str]:
    """Return all database file paths in the directory that match the suffix."""
    return [
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if f.endswith(suffix)
    ]


def compute_experience(conn: sqlite3.Connection) -> List[Tuple[str, int]]:
    """
    Calculate total experience for each faculty ID.
    Deduplicate entries and ignore zero-year spans.
    Returns: List of (faculty_id, experience_years)
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT vidwan_id, title, department, institution, start_year, end_year
        FROM experience
        WHERE start_year IS NOT NULL AND TRIM(start_year) <> '';
    """)
    rows = cursor.fetchall()

    experience_map = {}

    for vidwan_id, title, dept, inst, start, end in rows:
        try:
            start_year = int(start)
        except (TypeError, ValueError):
            continue

        if end is None or str(end).strip().lower() in ("", "present"):
            end_year = CURRENT_YEAR
        else:
            try:
                end_year = int(end)
            except ValueError:
                continue

        duration = max(0, end_year - start_year)
        if duration == 0:
            continue

        key = (vidwan_id, title.strip(), dept.strip(), inst.strip(), start_year, end_year)
        experience_map.setdefault(vidwan_id, 0)
        experience_map[vidwan_id] += duration

    return [(vid, exp if exp > 0 else None) for vid, exp in experience_map.items()]


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    cursor = conn.execute(f"PRAGMA table_info({table});")
    return column in [info[1] for info in cursor.fetchall()]


def update_experience(conn: sqlite3.Connection, data: List[Tuple[str, int]]) -> None:
    """Add or update 'experience_years' column in personal_info table."""
    if not column_exists(conn, "personal_info", "experience_years"):
        logging.info("Adding 'experience_years' column.")
        conn.execute("ALTER TABLE personal_info ADD COLUMN experience_years INTEGER;")

    update_stmt = "UPDATE personal_info SET experience_years = ? WHERE vidwan_id = ?;"
    conn.executemany(update_stmt, [(exp, vid) for vid, exp in data])
    conn.commit()


def process_database(db_path: str) -> None:
    """Process a single database safely."""
    logging.info(f"Processing {db_path}...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='experience';")
        if not cursor.fetchone():
            logging.warning(f"Skipping {db_path}: 'experience' table not found.")
            conn.close()
            return

        data = compute_experience(conn)
        if not data:
            logging.warning(f"No valid experience data in {db_path}.")
        else:
            update_experience(conn, data)
            logging.info(f"Updated experience for {len(data)} faculty entries.")

        conn.close()
        logging.info(f"Finished processing {db_path}.")

    except sqlite3.Error as e:
        logging.error(f"SQLite error in {db_path}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error in {db_path}: {e}")


def main() -> None:
    """Main entry point for processing all databases."""
    db_files = find_databases(DATABASE_DIR, DATABASE_SUFFIX)
    if not db_files:
        logging.warning(f"No databases found in {DATABASE_DIR} with suffix {DATABASE_SUFFIX}.")
        return

    logging.info(f"Found {len(db_files)} database(s) to process.")
    for db in db_files:
        process_database(db)

    logging.info("All databases processed.")


if __name__ == "__main__":
    main()
