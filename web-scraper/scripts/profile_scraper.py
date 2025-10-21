#!/usr/bin/env python3
"""
IRINS Multi-Institute Scraper

This script extracts faculty and researcher data from IRINS portals
across multiple Indian institutes. It reads institute URLs from a
SQLite database, crawls departmental and individual profile pages,
and stores structured data into institute-specific SQLite databases.

Author: Ayush Tripathi
Date: 2025-10-21
"""

from __future__ import annotations
import os
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

# ------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------
DB_DIR = "databases"
os.makedirs(DB_DIR, exist_ok=True)
DB_FILE = os.path.join(DB_DIR, "irins_institute_urls.db")
MAX_RETRIES: int = 3
RETRY_DELAY: int = 2
CRAWL_DELAY: float = 1.0

# ------------------------------------------------------------
# LOGGING CONFIGURATION
# ------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------
# DATA CLASSES
# ------------------------------------------------------------
@dataclass
class Institute:
    name: str
    url: str


@dataclass
class ProfileData:
    vidwan_id: str
    name: str
    designation: str
    institution: str
    personal: Dict[str, Optional[str]]
    pubs: Dict[str, int]
    metrics: Dict[str, int]
    alt: Dict[str, int]
    expertise: Dict[str, Optional[str]]
    experience: List[Tuple[str, str, str, str, str]]
    qualification: List[Tuple[str, str]]


# ------------------------------------------------------------
# UTILITY FUNCTIONS
# ------------------------------------------------------------
def safe_filename(name: str) -> str:
    """Return a filesystem-safe version of a name."""
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in name)


def fetch_html(url: str, retries: int = MAX_RETRIES, timeout: int = REQUEST_TIMEOUT) -> Optional[str]:
    """Fetch HTML content from a URL with retry logic."""
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            logger.warning("Attempt %d/%d failed for %s: %s", attempt, retries, url, exc)
            time.sleep(RETRY_DELAY)
    logger.error("Failed to fetch URL after %d retries: %s", retries, url)
    return None


# ------------------------------------------------------------
# PARSER FUNCTIONS
# ------------------------------------------------------------
def parse_irins_profile(html: str) -> ProfileData:
    """Parse the IRINS profile page and extract structured data."""
    soup = BeautifulSoup(html, "html.parser")

    # Basic info
    vidwan_tag = soup.find("h4", class_="ui-id")
    vidwan_id = None
    if vidwan_tag and (m := re.search(r"(\d+)", vidwan_tag.get_text())):
        vidwan_id = m.group(1)

    name_tag = soup.select_one("h1 strong")
    name = name_tag.get_text(strip=True) if name_tag else "Unknown"

    designation_tag = soup.select_one("i.fa-suitcase")
    designation = (
        designation_tag.next_sibling.strip() if designation_tag and designation_tag.next_sibling else "Unknown"
    )

    inst_tag = soup.select_one("i.fa-building")
    institution = (
        inst_tag.next_sibling.strip() if inst_tag and inst_tag.next_sibling else "Unknown"
    )

    # Fallback for designation/institution
    if designation.lower() == "unknown" or institution.lower() == "unknown":
        if nl_div := soup.select_one("div.name-location"):
            spans = nl_div.find_all("span", class_="col-sm-12")
            if spans:
                if designation.lower() == "unknown" and len(spans) > 1:
                    designation = spans[1].get_text(strip=True)
                if institution.lower() == "unknown":
                    institution = spans[-1].get_text(strip=True)

    # Personal details
    personal = {"gender": None, "address": None, "country": None, "website": None}
    if panel := soup.find("div", id="list_panel_personal"):
        if (g := panel.find("i", class_="fa-user")) and (span := g.find_next("span")):
            personal["gender"] = span.get_text(strip=True)
        if (h := panel.find("i", class_="fa-home")) and (span := h.find_next("span")):
            personal["address"] = span.get_text(strip=True)
        if (c := panel.find("i", class_="fa-map-marker")) and (span := c.find_next("span")):
            personal["country"] = span.get_text(strip=True)
        if (w := panel.find("span", id="p_p_url")) and (a := w.find("a")):
            personal["website"] = a["href"]

    # Publications
    pubs = {"Journal Articles": 0, "Conference Proceedings": 0, "Review": 0, "Others": 0}
    for li in soup.select(".profile_articles_part li"):
        num_tag = li.find("div", class_=["counter", "p0"])
        label = li.find("h6")
        if not num_tag or not label:
            continue
        count = int(re.sub(r"\D", "", num_tag.get_text()) or 0)
        txt = label.get_text().lower()
        if "journal" in txt:
            pubs["Journal Articles"] = count
        elif "conference" in txt:
            pubs["Conference Proceedings"] = count
        elif "review" in txt:
            pubs["Review"] = count
        else:
            pubs["Others"] += count

    # Metrics
    metrics = {"Citations": 0, "Crossref": 0, "h_index": 0, "coauthors": 0}
    for cell in soup.select(".Cell-citation"):
        if val := cell.find("span", class_="counter"):
            val_int = int(re.sub(r"\D", "", val.text) or 0)
            txt = cell.get_text().lower()
            if "crossref" in txt:
                metrics["Crossref"] = val_int
            elif "h-index" in txt:
                metrics["h_index"] = val_int
            elif "citation" in txt and metrics["Citations"] == 0:
                metrics["Citations"] = val_int
    if coauth_btn := soup.find("button", string=re.compile("Co-author")):
        if m := re.search(r"(\d+)", coauth_btn.text):
            metrics["coauthors"] = int(m.group(1))

    # Altmetrics
    alt = {"news": 0, "facebook": 0, "twitter": 0, "mendeley": 0, "google_plus": 0}
    for icon in soup.select(".profile-bio i"):
        cls = icon.get("class", [])
        if span := icon.find_next("span", class_="counter"):
            count = int(re.sub(r"\D", "", span.text) or 0)
            if "fa-newspaper" in cls:
                alt["news"] = count
            elif "fa-facebook" in cls:
                alt["facebook"] = count
            elif "fa-twitter" in cls:
                alt["twitter"] = count
            elif "fa-mendeley" in cls:
                alt["mendeley"] = count
            elif "fa-google-plus" in cls:
                alt["google_plus"] = count

    # Expertise
    exp_main = soup.find("span", id="e_expertise")
    exp_sub = soup.find("h5", id="e_s_expertise")
    expertise = {
        "main_expertise": exp_main.get_text(strip=True) if exp_main else None,
        "sub_expertise": exp_sub.get_text(strip=True) if exp_sub else None,
    }

    # Experience
    experience = []
    for li in soup.select("#list_panel_experience li"):
        title = li.find("h2")
        ps = li.find_all("p")
        time_el = li.find("time")
        start, end = (time_el.get_text(strip=True).split("-") + [None])[:2] if time_el else ("", "")
        experience.append(
            (
                title.get_text(strip=True) if title else None,
                ps[0].get_text(strip=True) if len(ps) > 0 else None,
                ps[1].get_text(strip=True) if len(ps) > 1 else None,
                start.strip(),
                (end or "").strip(),
            )
        )

    # Qualification
    qualification = [
        (li.find("h2").get_text(strip=True), li.find("p").get_text(strip=True))
        for li in soup.select("#list_panel_qualification li")
        if li.find("h2") and li.find("p")
    ]

    return ProfileData(
        vidwan_id=vidwan_id or f"VIDWAN_{int(time.time() * 1000)}",
        name=name,
        designation=designation,
        institution=institution,
        personal=personal,
        pubs=pubs,
        metrics=metrics,
        alt=alt,
        expertise=expertise,
        experience=experience,
        qualification=qualification,
    )


# ------------------------------------------------------------
# MAIN SCRAPER LOGIC
# ------------------------------------------------------------
def fetch_institutes(db_path: str) -> List[Institute]:
    """Load institute names and IRINS URLs from a SQLite database."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT institute_name, irins_url FROM institutes WHERE irins_url IS NOT NULL")
        return [Institute(name, url) for name, url in cur.fetchall()]


def create_connection(db_path: str) -> sqlite3.Connection:
    """Create a SQLite connection with foreign key support."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def crawl_institute(inst: Institute) -> None:
    """Crawl departments and profiles for a given institute."""
    logger.info("Processing institute: %s (%s)", inst.name, inst.url)
    safe_name = safe_filename(inst.name)
    profiles_db = os.path.join(DB_DIR, f"{safe_name}.db")
    data_db = os.path.join(BASE_DIR, "databases", f"{safe_name}_profiles.db")

    # Fetch departments
    html = fetch_html(inst.url)
    if not html:
        logger.error("Skipping %s — unable to fetch homepage", inst.name)
        return

    soup = BeautifulSoup(html, "html.parser")
    departments = [
        (a.get_text(strip=True), a.get("href"))
        for a in soup.select("ul#sidebar-nav li a[href]")
        if a["href"].startswith("http")
    ]
    logger.info("Found %d departments for %s", len(departments), inst.name)

    # Save profile URLs
    with create_connection(profiles_db) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                department_name TEXT,
                department_url TEXT,
                profile_url TEXT UNIQUE
            )
        """)
        for dept_name, dept_url in departments:
            dept_html = fetch_html(dept_url)
            if not dept_html:
                continue
            dept_soup = BeautifulSoup(dept_html, "html.parser")
            profile_links = [
                a["href"]
                for a in dept_soup.select("a.btn-u.btn-u-xs.btn-u-sea.mt10[href]")
                if "profile" in a["href"]
            ]
            for url in profile_links:
                conn.execute(
                    "INSERT OR IGNORE INTO profiles (department_name, department_url, profile_url) VALUES (?, ?, ?)",
                    (dept_name, dept_url, url),
                )
            conn.commit()
            time.sleep(CRAWL_DELAY)
    logger.info("Profile URLs saved for %s", inst.name)

    # Extract and store profile data
    with create_connection(profiles_db) as conn:
        cur = conn.cursor()
        cur.execute("SELECT profile_url FROM profiles")
        profile_urls = [r[0] for r in cur.fetchall()]

    with create_connection(data_db) as conn:
        cur = conn.cursor()
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS personal_info (
            vidwan_id TEXT PRIMARY KEY,
            name TEXT,
            designation TEXT,
            institution TEXT
        );
        CREATE TABLE IF NOT EXISTS personal_details (
            vidwan_id TEXT,
            gender TEXT,
            address TEXT,
            country TEXT,
            website TEXT,
            FOREIGN KEY (vidwan_id) REFERENCES personal_info(vidwan_id)
        );
        CREATE TABLE IF NOT EXISTS publications (
            vidwan_id TEXT,
            journal_articles INTEGER,
            conference_proceedings INTEGER,
            reviews INTEGER,
            others INTEGER,
            FOREIGN KEY (vidwan_id) REFERENCES personal_info(vidwan_id)
        );
        CREATE TABLE IF NOT EXISTS metrics (
            vidwan_id TEXT,
            total_citations INTEGER,
            crossref_citations INTEGER,
            h_index INTEGER,
            coauthors INTEGER,
            FOREIGN KEY (vidwan_id) REFERENCES personal_info(vidwan_id)
        );
        CREATE TABLE IF NOT EXISTS altmetrics (
            vidwan_id TEXT,
            news INTEGER,
            facebook INTEGER,
            twitter INTEGER,
            mendeley INTEGER,
            google_plus INTEGER,
            FOREIGN KEY (vidwan_id) REFERENCES personal_info(vidwan_id)
        );
        CREATE TABLE IF NOT EXISTS expertise (
            vidwan_id TEXT,
            main_expertise TEXT,
            sub_expertise TEXT,
            FOREIGN KEY (vidwan_id) REFERENCES personal_info(vidwan_id)
        );
        CREATE TABLE IF NOT EXISTS experience (
            vidwan_id TEXT,
            title TEXT,
            department TEXT,
            institution TEXT,
            start_year TEXT,
            end_year TEXT,
            FOREIGN KEY (vidwan_id) REFERENCES personal_info(vidwan_id)
        );
        CREATE TABLE IF NOT EXISTS qualification (
            vidwan_id TEXT,
            degree TEXT,
            institution TEXT,
            FOREIGN KEY (vidwan_id) REFERENCES personal_info(vidwan_id)
        );
        """)
        conn.commit()

        for i, url in enumerate(profile_urls, 1):
            logger.info("[%d/%d] Fetching %s", i, len(profile_urls), url)
            html = fetch_html(url)
            if not html:
                continue
            data = parse_irins_profile(html)
            insert_profile_data(conn, data)
            logger.info("Stored profile: %s (%s)", data.name, data.vidwan_id)
            time.sleep(CRAWL_DELAY)
    logger.info("Completed data extraction for %s", inst.name)


def insert_profile_data(conn: sqlite3.Connection, data: ProfileData) -> None:
    """Insert profile data into the SQLite database."""
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO personal_info VALUES (?, ?, ?, ?)",
                (data.vidwan_id, data.name, data.designation, data.institution))
    cur.execute("INSERT OR REPLACE INTO personal_details VALUES (?, ?, ?, ?, ?)",
                (data.vidwan_id, data.personal["gender"], data.personal["address"],
                 data.personal["country"], data.personal["website"]))
    cur.execute("INSERT OR REPLACE INTO publications VALUES (?, ?, ?, ?, ?)",
                (data.vidwan_id, data.pubs["Journal Articles"], data.pubs["Conference Proceedings"],
                 data.pubs["Review"], data.pubs["Others"]))
    cur.execute("INSERT OR REPLACE INTO metrics VALUES (?, ?, ?, ?, ?)",
                (data.vidwan_id, data.metrics["Citations"], data.metrics["Crossref"],
                 data.metrics["h_index"], data.metrics["coauthors"]))
    cur.execute("INSERT OR REPLACE INTO altmetrics VALUES (?, ?, ?, ?, ?, ?)",
                (data.vidwan_id, data.alt["news"], data.alt["facebook"], data.alt["twitter"],
                 data.alt["mendeley"], data.alt["google_plus"]))
    cur.execute("INSERT OR REPLACE INTO expertise VALUES (?, ?, ?)",
                (data.vidwan_id, data.expertise["main_expertise"], data.expertise["sub_expertise"]))
    for exp in data.experience:
        cur.execute("INSERT INTO experience VALUES (?, ?, ?, ?, ?, ?)", (data.vidwan_id, *exp))
    for qual in data.qualification:
        cur.execute("INSERT INTO qualification VALUES (?, ?, ?)", (data.vidwan_id, *qual))
    conn.commit()


# ------------------------------------------------------------
# ENTRY POINT
# ------------------------------------------------------------
def main() -> None:
    institutes = fetch_institutes(DB_FILE)
    logger.info("Found %d institutes with valid IRINS URLs", len(institutes))
    for inst in institutes:
        crawl_institute(inst)


if __name__ == "__main__":
    main()
