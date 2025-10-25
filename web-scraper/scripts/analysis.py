#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Researcher Flow Analysis
-----------------------
Analyzes researcher career data from SQLite databases.
Features:
- Computes average and median metrics (overall and by designation/field)
- Generates plots: experience histogram, gender pie chart, PhD country distribution
- Outputs results to a new SQLite database and PNG charts per input database
"""

import os
import glob
import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
import logging
import numpy as np

# ---------------- Logging Setup ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("ResearcherFlowAnalysis")

# ---------------- SQL Helper Functions ----------------
def safe_median(cursor, table, column, where_clause=""):
    """
    Compute median for a numeric column safely.
    Returns None if there are no rows or any error occurs.
    """
    try:
        query = f"SELECT {column} FROM {table} {where_clause} ORDER BY {column}"
        cursor.execute(query)
        rows = cursor.fetchall()
        n = len(rows)
        if n == 0:
            return None
        # Median calculation
        mid = n // 2
        if n % 2 == 1:
            return rows[mid][0]
        return (rows[mid - 1][0] + rows[mid][0]) / 2
    except Exception as e:
        logger.warning("Failed to compute median for %s: %s", column, e)
        return None

def compute_avg_median(conn, table, column, where_clause=""):
    """Return both average and median of a column in a table."""
    cursor = conn.cursor()
    cursor.execute(f"SELECT AVG({column}) FROM {table} {where_clause}")
    avg_value = cursor.fetchone()[0]
    median_value = safe_median(cursor, table, column, where_clause)
    return {"average": avg_value, "median": median_value}

def compute_group_stats(conn, table, group_col, numeric_cols):
    """
    Compute average and median statistics for each group in a column.
    Returns a pandas DataFrame.
    """
    cursor = conn.cursor()
    cursor.execute(f"SELECT DISTINCT {group_col} FROM {table} WHERE {group_col} IS NOT NULL")
    groups = [r[0] for r in cursor.fetchall()]

    stats_list = []
    for group in groups:
        stats = {group_col: group}
        for col in numeric_cols:
            where_clause = f"WHERE {col} IS NOT NULL AND {group_col}='{group}'"
            result = compute_avg_median(conn, table, col, where_clause)
            stats[f"{col}_avg"] = result["average"]
            stats[f"{col}_median"] = result["median"]
        stats_list.append(stats)

    return pd.DataFrame(stats_list)

# ---------------- Plotting Functions ----------------
def plot_gender_distribution(df, output_folder):
    """Create a pie chart of gender distribution."""
    if "gender" not in df.columns:
        return

    counts = df["gender"].str.strip().str.title().value_counts()
    if counts.empty:
        return

    plt.figure(figsize=(6,6))
    counts.plot.pie(autopct="%1.1f%%", colors=["#66b3ff","#ff9999","#99ff99"])
    plt.ylabel("")
    plt.title("Gender Distribution")
    plt.tight_layout()
    plt.savefig(os.path.join(output_folder, "gender_distribution.png"))
    plt.close()

def plot_experience_histogram(df, output_folder):
    """Plot histogram of researcher experience in years (capped at 80)."""
    if "experience_years" not in df.columns:
        return

    exp = df["experience_years"].dropna()
    if exp.empty:
        return

    exp_capped = exp.clip(upper=80)
    bins = range(0, 82)  # 0-80 inclusive
    counts, bin_edges = np.histogram(exp_capped, bins=bins)

    plt.figure(figsize=(12,6))
    plt.bar(bin_edges[:-1], counts, width=1, edgecolor="black", color="#66b3ff", align="edge")

    # Annotate bars with counts
    for i, count in enumerate(counts):
        if count > 0:
            plt.text(bin_edges[i] + 0.5, count + 0.5, str(count), ha='center', va='bottom', fontsize=8)

    plt.xticks(range(0, 81, 5))
    plt.xlabel("Experience Years")
    plt.ylabel("Number of Researchers")
    plt.title("Experience Years Distribution")
    plt.tight_layout()
    plt.savefig(os.path.join(output_folder, "experience_histogram.png"))
    plt.close()

def plot_phd_country_distribution(df, output_folder):
    """Plot bar chart of PhD countries."""
    if "phd_country" not in df.columns:
        return

    countries = df["phd_country"].dropna().str.strip().str.title()
    countries = countries.replace({"United States Of America":"United States", "Usa":"United States"})
    counts = countries.value_counts()
    if counts.empty:
        return

    plt.figure(figsize=(12,6))
    bars = plt.bar(counts.index, counts.values, edgecolor="black", color="#66b3ff")

    for bar in bars:
        height = bar.get_height()
        if height > 0:
            plt.text(bar.get_x() + bar.get_width()/2, height + 0.5, str(int(height)), ha='center', va='bottom', fontsize=8)

    plt.ylabel("Number of Researchers")
    plt.xlabel("PhD Country")
    plt.title("PhD Country Distribution")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.savefig(os.path.join(output_folder, "phd_country_distribution.png"))
    plt.close()

# ---------------- Main Analysis Function ----------------
def main():
    db_files = glob.glob("databases/researcher_flows*.db")
    if not db_files:
        logger.warning("No databases found in 'databases/' folder.")
        return

    numeric_columns = ["h_index", "total_citations", "num_publications", "experience_years"]

    for db_path in db_files:
        db_name = os.path.splitext(os.path.basename(db_path))[0]
        output_folder = os.path.join(f"analysis_{db_name}")
        os.makedirs(output_folder, exist_ok=True)

        conn = sqlite3.connect(db_path)
        df = pd.read_sql_query("SELECT * FROM career_flows", conn)

        if df.empty:
            logger.warning("Database %s is empty. Skipping.", db_path)
            conn.close()
            continue

        # Clean and normalize key columns
        if "gender" in df.columns:
            df["gender"] = df["gender"].str.strip().str.title()
        if "phd_country" in df.columns:
            df["phd_country"] = df["phd_country"].str.strip().str.title().replace(
                {"United States Of America": "United States", "Usa": "United States"}
            )

        # Overall statistics
        overall_stats = {col: compute_avg_median(conn, "career_flows", col) for col in numeric_columns}
        overall_df = pd.DataFrame(overall_stats).T.reset_index().rename(columns={"index": "metric"})
        overall_conn = sqlite3.connect(os.path.join(output_folder, f"{db_name}_analysis.db"))
        overall_df.to_sql("overall_stats", overall_conn, if_exists="replace", index=False)

        # Statistics by designation
        if "designation" in df.columns:
            designation_df = compute_group_stats(conn, "career_flows", "designation", numeric_columns)
            designation_df.to_sql("designation_stats", overall_conn, if_exists="replace", index=False)

        # Statistics by field
        if "field" in df.columns:
            field_df = compute_group_stats(conn, "career_flows", "field", numeric_columns)
            field_df.to_sql("field_stats", overall_conn, if_exists="replace", index=False)

        # Generate plots
        plot_gender_distribution(df, output_folder)
        plot_experience_histogram(df, output_folder)
        plot_phd_country_distribution(df, output_folder)

        conn.close()
        overall_conn.close()
        logger.info("Completed analysis for database: %s", db_name)

if __name__ == "__main__":
    main()
