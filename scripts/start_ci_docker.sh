#!/bin/bash
set -e
# Build the image (only needed if you haven’t pushed/pulled it already)
docker build -t goncaloferreirauva/gd-ci-service -f ./ci_calc_service/Dockerfile ./ci_calc_service
# Run the container
docker run --rm -t -p 8011:8011 -d \
  --env AUTH_VERIFY_URL=http://127.0.0.1:8000/gd-cim-api/verify_token \
  --env-file .env \
  goncaloferreirauva/gd-ci-service \
  uvicorn main:app --host 0.0.0.0 --port 8011
