Replay fixtures made with `local-worker incident JOB --fixture DIR`: each directory holds `fixture.json` and `files/`, and `tests/test_incident_fixtures.py`
replays every one through the real edit pipeline with the recorded model replies. Fixtures contain private source and raw model output, so review and trim
one before committing it. Set `expected` in `fixture.json` once the incident is fixed.
