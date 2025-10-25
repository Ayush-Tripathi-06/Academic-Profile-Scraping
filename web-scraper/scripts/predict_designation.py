#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Random Forest Classifier for Academic Career Level Prediction
Includes text embeddings, interaction features, and balanced sampling.

"""

from __future__ import annotations
import os, sqlite3, numpy as np, pandas as pd, logging
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.feature_extraction.text import TfidfVectorizer
from imblearn.ensemble import BalancedRandomForestClassifier
from imblearn.over_sampling import RandomOverSampler
from sklearn.metrics import classification_report, confusion_matrix
import joblib

# --------------------------
# Logging & directories setup
# --------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("AcademicLevelPredictor")

DB_FILE = "databases/researcher_flows.db"
OUTPUT_FOLDER = "model_outputs"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# --------------------------
# Load dataset from SQLite
# --------------------------
conn = sqlite3.connect(DB_FILE)
data = pd.read_sql_query("SELECT * FROM career_flows", conn)
conn.close()
logger.info("Dataset loaded: %d records", len(data))

# --------------------------
# Standardize designations
# --------------------------
def clean_designation(title: str) -> str:
    if not isinstance(title, str):
        return "Other"
    title = title.lower()
    if "assistant" in title:
        return "Assistant Professor"
    elif "associate" in title:
        return "Associate Professor"
    elif "professor" in title:
        return "Professor"
    return "Other"

data["designation"] = data["designation"].apply(clean_designation)
data = data[data["designation"].isin(["Assistant Professor", "Associate Professor", "Professor"])]

# --------------------------
# Prepare numeric columns
# --------------------------
numeric_cols = [
    "experience_years", "num_publications", "h_index",
    "total_citations", "publications_per_year",
    "phd_lat", "phd_lon", "curr_lat", "curr_lon",
    "journal_articles", "conference_proceedings", "reviews", "others"
]
for col in numeric_cols:
    data[col] = pd.to_numeric(data.get(col, np.nan), errors="coerce")

# Treat zeros as missing for key metrics
for col in ["num_publications", "h_index", "total_citations"]:
    data.loc[data[col] == 0, col] = np.nan

# --------------------------
# Compute geographical distance
# --------------------------
def compute_distance(lat1, lon1, lat2, lon2):
    R = 6371
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat/2)**2 + np.cos(lat1)*np.cos(lat2)*np.sin(dlon/2)**2
    return 2*R*np.arcsin(np.sqrt(a))

data["phd_to_current_km"] = compute_distance(data["phd_lat"], data["phd_lon"], data["curr_lat"], data["curr_lon"])

# --------------------------
# Normalize previous titles
# --------------------------
def clean_prev_title(title: str) -> str:
    if not isinstance(title, str) or not title.strip():
        return "Unknown"
    title = title.lower()
    if "assistant" in title:
        return "Assistant Professor"
    elif "associate" in title:
        return "Associate Professor"
    elif "professor" in title:
        return "Professor"
    return "Unknown"

data["previous_title_clean"] = data["previous_title"].apply(clean_prev_title)

# --------------------------
# Career progression metric
# --------------------------
def progression_score(prev: str, current: str) -> int:
    if prev == "Unknown": 
        return 0
    mapping = {
        ("Assistant Professor","Associate Professor"):2,
        ("Associate Professor","Professor"):2,
        ("Assistant Professor","Professor"):-1,
        ("Professor","Professor"):1,
        ("Associate Professor","Associate Professor"):1,
        ("Assistant Professor","Assistant Professor"):1,
        ("Professor","Associate Professor"):-2,
        ("Professor","Assistant Professor"):-3
    }
    return mapping.get((prev, current),0)

data["career_score"] = data.apply(lambda r: progression_score(r["previous_title_clean"], r["designation"]), axis=1)
data["career_score"] = data["career_score"].clip(-2,2)

# --------------------------
# Publication type ratios
# --------------------------
data["journal_ratio"] = data["journal_articles"]/data["num_publications"]
data["conference_ratio"] = data["conference_proceedings"]/data["num_publications"]
data["review_ratio"] = data["reviews"]/data["num_publications"]
data["other_ratio"] = data["others"]/data["num_publications"]
for col in ["journal_ratio","conference_ratio","review_ratio","other_ratio"]:
    data[col] = data[col].fillna(0)

# --------------------------
# Top previous institutions one-hot
# --------------------------
top_institutions = data["previous_institution"].value_counts().nlargest(10).index
data["prev_inst_top"] = data["previous_institution"].where(data["previous_institution"].isin(top_institutions), "Other")
inst_dummies = pd.get_dummies(data["prev_inst_top"], prefix="prev_inst")
data = pd.concat([data, inst_dummies], axis=1)

# --------------------------
# Interaction with previous titles
# --------------------------
prev_dummies = pd.get_dummies(data["previous_title_clean"])
if "Unknown" in prev_dummies.columns:
    prev_dummies = prev_dummies.drop(columns=["Unknown"])
data = pd.concat([data, prev_dummies], axis=1)
for col in prev_dummies.columns:
    data[f"{col}_x_exp"] = data[col] * data["experience_years"]

# --------------------------
# Text embeddings for research field
# --------------------------
tfidf_vectorizer = TfidfVectorizer(max_features=20)
field_features = tfidf_vectorizer.fit_transform(data["field"].fillna("unknown")).toarray()
field_cols = [f"field_{i}" for i in range(field_features.shape[1])]
data[field_cols] = field_features

# --------------------------
# Interaction features
# --------------------------
data["exp_x_pubrate"] = data["experience_years"] * data["publications_per_year"]
data["career_x_pub"] = data["career_score"] * data["num_publications"].fillna(0)

# --------------------------
# Prepare feature matrix
# --------------------------
feature_cols = [
    "experience_years", "publications_per_year", "num_publications",
    "h_index", "total_citations", "phd_to_current_km",
    "career_score","journal_ratio","conference_ratio","review_ratio","other_ratio",
    "exp_x_pubrate","career_x_pub"
] + list(inst_dummies.columns) + [f"{c}_x_exp" for c in prev_dummies.columns] + field_cols

X = data[feature_cols]
y = data["designation"]

# Impute missing values
imputer = SimpleImputer(strategy="median")
X = pd.DataFrame(imputer.fit_transform(X), columns=X.columns)

# Encode target labels
label_encoder = LabelEncoder()
y_enc = label_encoder.fit_transform(y)

# --------------------------
# Split & oversample minority classes
# --------------------------
X_train, X_test, y_train, y_test = train_test_split(X, y_enc, test_size=0.25, random_state=42, stratify=y_enc)
ros = RandomOverSampler(random_state=42)
X_train, y_train = ros.fit_resample(X_train, y_train)

# --------------------------
# Build & train balanced Random Forest
# --------------------------
pipeline = Pipeline([
    ("scaler", StandardScaler()),
    ("brf", BalancedRandomForestClassifier(
        n_estimators=1500,
        max_depth=25,
        min_samples_split=4,
        min_samples_leaf=2,
        max_features="sqrt",
        random_state=42,
        n_jobs=-1
    ))
])

logger.info("Starting training of Balanced Random Forest...")
pipeline.fit(X_train, y_train)
logger.info("Training finished successfully.")

# --------------------------
# Evaluate model
# --------------------------
y_pred = pipeline.predict(X_test)
report = classification_report(y_test, y_pred, target_names=label_encoder.classes_)
logger.info("\n%s", report)

cm = confusion_matrix(y_test, y_pred)
logger.info("Confusion matrix:\n%s", cm)

# --------------------------
# Save outputs
# --------------------------
with open(os.path.join(OUTPUT_FOLDER, "classification_report.txt"), "w") as f:
    f.write(report)

rf_model = pipeline.named_steps["brf"]
feat_importances = pd.DataFrame({
    "feature": X.columns,
    "importance": rf_model.feature_importances_
}).sort_values("importance", ascending=False)
feat_importances.to_csv(os.path.join(OUTPUT_FOLDER, "feature_importances.csv"), index=False)

plt.figure(figsize=(10,6))
sns.barplot(y="feature", x="importance", data=feat_importances.head(20))
plt.title("Top 20 Features - Random Forest")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_FOLDER, "feature_importances.png"), dpi=300)
plt.close()

plt.figure(figsize=(6,5))
sns.heatmap(cm, annot=True, fmt="d", cmap="Blues",
            xticklabels=label_encoder.classes_, yticklabels=label_encoder.classes_)
plt.xlabel("Predicted")
plt.ylabel("Actual")
plt.title("Confusion Matrix")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_FOLDER, "confusion_matrix.png"), dpi=300)
plt.close()

joblib.dump(pipeline, os.path.join(OUTPUT_FOLDER, "rf_academic_model.pkl"))
joblib.dump(label_encoder, os.path.join(OUTPUT_FOLDER, "label_encoder.pkl"))

logger.info("All outputs and model saved in '%s'", OUTPUT_FOLDER)
