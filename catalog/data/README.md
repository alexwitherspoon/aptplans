Airport identity is fetched, not committed.

Origin writes `/var/lib/aptplans/catalog/airports.jsonl` from FAA NASR APT (public-use airports and seaplane bases) merged with NPIAS Appendix A, and `/var/lib/aptplans/catalog/grants.jsonl` from FAA AIP grant histories. NASR is the superset we would consider. NPIAS marks which of those airports are more likely to publish a master plan or ALP. Terms: [FAA terms and systems](../../docs/FAA.md).

Refresh on the origin when an overlay is missing, empty, or not written this calendar month (`python3 -m pipeline.refresh_airports`). GitHub Actions must not live-fetch FAA; CI and `make site` without an overlay use the reference cases only.

Do not add origin airport JSONL or corpus files here.
