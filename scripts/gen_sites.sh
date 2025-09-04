export $(grep -v '^#' .env | xargs) # To export the environment variables (for authentication)
python3 goc_db_fetch_service/fetch_goc_db.py --format json --output output/sites_raw.json --scope EGI