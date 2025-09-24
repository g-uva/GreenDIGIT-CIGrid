#!/bin/bash
set -e
docker build -t goncaloferreirauva/gd-ci-service:latest -f ./ci_calc_service/Dockerfile ./ci_calc_service
docker push goncaloferreirauva/gd-ci-service:latest