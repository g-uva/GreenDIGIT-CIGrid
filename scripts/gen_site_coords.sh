export $(grep -v '^#' .env | xargs) # To export the environment variables (for authentication)
python goc_db_fetch_service/fetch_goc_db.py --format json --output output/site_latlng.json --scope EGI