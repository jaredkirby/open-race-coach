# Test Fixtures

This directory is reserved for captured simulator evidence used by CI-runnable tests.

Expected v0 fixture types:

- raw AMS2 Project CARS 2 shared-memory dumps captured on Windows during Phase 1 validation
- raw ACC shared-memory page dumps captured on Windows during Phase 4 validation
- shared-memory-derived JSON from public Project CARS/Project CARS 2 tools, when provenance is recorded and the test is explicit about missing raw mmap fields
- known-good recorded traces with hand-checked lap timing and corner/delta expectations

Do not fabricate binary fixture dumps. Until live captures exist, adapter tests should use small in-memory `ctypes` fixtures, synthetic traces, or clearly labeled shared-memory-derived public samples.

On Windows, capture candidates into ignored scratch storage first:

```bash
uv run python scripts/capture_ams2_fixture.py --count 5
uv run python scripts/capture_acc_fixture.py --count 5
```

The default destinations are `.scratch/fixtures/ams2/` and `.scratch/fixtures/acc/`. Raw shared-memory pages can contain driver and multiplayer participant names plus simulator, session, or machine-specific data. Review the bytes and sidecars for privacy, provenance, hashes, struct sizes, and stable regression value before promoting a minimal set into `tests/fixtures/`. Promotion is manual and must use an explicit `--out` path; the repository does not provide an automated redaction tool. Do not commit unreviewed captures.

When raw sidecars are committed under `tests/fixtures/ams2/` or `tests/fixtures/acc/`, `tests/test_raw_fixtures.py` parses the corresponding `.bin` files in CI. The tests intentionally skip while no live captures exist.
