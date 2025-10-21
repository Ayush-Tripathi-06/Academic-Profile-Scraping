#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IRINS Institute Scraper

Fetches institute data from the IRINS portal, filters by
specific categories (IITs, NITs, IIMs, IISERs, R&D Institutions, Other INIs),
and stores the results in a fresh SQLite database.

Example:
    $ python scraper.py

Author: Ayush Tripathi
Created: 2025-10-20
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any, Dict, List, Optional

import requests

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------
PAGE_URL: str = "https://irins.inflibnet.ac.in/instances"
DB_FILE: str = "irins_institute_urls.db"

TARGET_CATEGORIES: set[str] = {
    "IITs & IISc",
    "NITs",
    "R & D Institutions",
    "Other INIs",
    "IIMs",
    "IISERs",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# -------------------------------------------------------------------
# Core Functions
# -------------------------------------------------------------------
def fetch_page(url: str, timeout: int = 30) -> str:
    """Fetch HTML content from a given URL.

    Args:
        url (str): The URL to fetch.
        timeout (int, optional): Timeout duration in seconds. Defaults to 30.

    Returns:
        str: The raw HTML content of the page.

    Raises:
        requests.RequestException: If a network or HTTP error occurs.
    """
    logging.info("Fetching page: %s", url)
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        logging.error("Failed to fetch page from %s: %s", url, exc)
        raise


def extract_json_from_scripts(html: str) -> Optional[List[Dict[str, Any]]]:
    """Extract embedded JSON data from the HTML page.

    Args:
        html (str): The raw HTML source code.

    Returns:
        Optional[List[Dict[str, Any]]]: Parsed JSON data if found, else None.
    """
    patterns = [
        r"window\.configData\s*=\s*(\[\s*\{.*?\}\s*\])\s*;",
        r"configData\s*=\s*(\[\s*\{.*?\}\s*\])\s*;",
    ]

    for pattern in patterns:
        match = re.search(pattern, html, flags=re.DOTALL)
        if not match:
            continue

        raw_json = match.group(1)
        try:
            return json.loads(raw_json)
        except json.JSONDecodeError:
            # Clean up common JS syntax issues for JSON compatibility
            cleaned = (
                raw_json.replace("'", '"')
                .replace(",}", "}")
                .replace(",]", "]")
            )
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                logging.warning("Failed to decode JSON using pattern: %s", pattern)
                continue

    logging.error("No valid JSON block found in page scripts.")
    return None


def build_irins_url(uname: Optional[str]) -> Optional[str]:
    """Construct the IRINS URL for a given institute username."""
    return f"https://{uname}.irins.org" if uname else None


def normalize_data(json_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter and normalize IRINS institute data.

    Args:
        json_entries (List[Dict[str, Any]]): Raw JSON data entries.

    Returns:
        List[Dict[str, Any]]: Filtered and enriched data records.
    """
    seen_unames: set[str] = set()
    filtered: list[Dict[str, Any]] = []

    for record in json_entries or []:
        uname = record.get("uname")
        org_type = record.get("org_type")

        if not uname or uname in seen_unames or org_type not in TARGET_CATEGORIES:
            continue

        record["irins_url"] = build_irins_url(uname)
        seen_unames.add(uname)
        filtered.append(record)

    return filtered


def save_to_db(records: List[Dict[str, Any]], db_file: str) -> None:
    """Save normalized institute data into a SQLite database.

    Args:
        records (List[Dict[str, Any]]): List of filtered institute records.
        db_file (str): Output SQLite database file path.
    """
    if not records:
        logging.warning("No records to save. Database creation skipped.")
        return

    logging.info("Saving %d records to database: %s", len(records), db_file)
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("DROP TABLE IF EXISTS institutes")

    cur.execute("""
        CREATE TABLE institutes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uname TEXT,
            institute_id TEXT,
            institute_name TEXT,
            org_type TEXT,
            district TEXT,
            state TEXT,
            latitude TEXT,
            longitude TEXT,
            logo_path TEXT,
            irins_url TEXT,
            raw_json TEXT
        )
    """)

    insert_query = """
        INSERT INTO institutes
        (uname, institute_id, institute_name, org_type, district, state,
         latitude, longitude, logo_path, irins_url, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    for rec in records:
        cur.execute(
            insert_query,
            (
                rec.get("uname"),
                rec.get("institute_id"),
                rec.get("institute_name"),
                rec.get("org_type"),
                rec.get("district"),
                rec.get("state"),
                rec.get("lattitude") or rec.get("latitude"),
                rec.get("longitude"),
                rec.get("logo_path"),
                rec.get("irins_url"),
                json.dumps(rec, ensure_ascii=False),
            ),
        )

    conn.commit()
    conn.close()
    logging.info("Database successfully saved with %d records.", len(records))


# -------------------------------------------------------------------
# Entry Point
# -------------------------------------------------------------------
def main() -> None:
    """Execute the full scraping and data storage workflow."""
    logging.info("Starting IRINS institute scraper...")

    try:
        html = fetch_page(PAGE_URL)
        json_data = extract_json_from_scripts(html)

        if not json_data:
            logging.error("No JSON data extracted from the page.")
            return

        filtered_records = normalize_data(json_data)
        logging.info("Filtered %d institutes matching target categories.", len(filtered_records))
        save_to_db(filtered_records, DB_FILE)

    except Exception as exc:
        logging.exception("Unexpected error during scraping: %s", exc)

    logging.info("IRINS scraping process completed.")


if __name__ == "__main__":
    main()
