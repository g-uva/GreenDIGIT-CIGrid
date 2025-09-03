## CI to CFP
- [ ] Write description here. :)

### Roadmap
- Per-job CFP: wire in your workload energy estimates (kWh) to output absolute grams CO₂e per job/window.
- Dynamic PUE: add a simple table (per site) to override the static 1.4 when you obtain better values; later, call a PUE data source when available.
- Fallback provider: optionally add providers like WattTime or UK’s Carbon Intensity API for regions where Electricity Maps isn’t available.
- API hygiene: respect rate limits with a small backoff; cache per-site responses for 10–15 minutes to avoid redundant calls.