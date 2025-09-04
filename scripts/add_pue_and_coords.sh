#!/bin/bash
mkdir -p output
python3 gocdb_postprocess/gocdb_postprocess.py output/sites_raw.json output/sites_latlngpue.json