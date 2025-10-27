 # Academic-Profile-Scraping
institute_url_scraper.py fetches institute data from the IRINS portal and filters by specific categories (IITs, NITs, IIMs, IISERs, R&D Institutions, Other INIs), and stores the results in a fresh SQLite database.

profile_scraper.py extracts faculty and researcher data from IRINS portals across multiple Indian institutes. It reads institute URLs from a SQLite database, crawls departmental and individual profile pages, and stores structured data into institute-specific SQLite databases.

update_experience.py updates faculty experience across all profile databases. 
Features of update_experience.py:
  - Deduplicate identical experience entries.
  - Compute total experience per faculty.
  - Replace zero-year experiences with NULL.
  - Safe, logged, modular workflow
    
update_geolocation.py updates geolocation data using OpenCage API and updates *_profiles.db databases by adding geolocation(country, state, city, latitude, longitude, full_address) to the qualification table for institutions.

predict_designation.py uses Random Forest Classifier for Academic Career Level Prediction.It Includes text embeddings, interaction features, and balanced sampling.

merge_data.py builds Researcher Career Flow Database and filters institutes by organization type and aggregates researcher career data.

researcher_flows.py builds. Researcher Career Flow Database.Aggregates data from multiple *_profiles.db files (one per institute) and combines them with IRINS institute coordinates to create a single SQLite database.
