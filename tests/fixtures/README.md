# Test Fixtures

This directory is reserved for captured simulator evidence used by CI-runnable tests.

Expected v0 fixture types:

- raw AMS2 Project CARS 2 shared-memory dumps captured on Windows during Phase 1 validation
- raw ACC shared-memory page dumps captured on Windows during Phase 4 validation
- shared-memory-derived JSON from public Project CARS/Project CARS 2 tools, when provenance is recorded and the test is explicit about missing raw mmap fields
- known-good recorded traces with hand-checked lap timing and corner/delta expectations

Do not fabricate binary fixture dumps. Until live captures exist, adapter tests should use small in-memory `ctypes` fixtures, synthetic traces, or clearly labeled shared-memory-derived public samples.

Use `uv run python scripts/capture_ams2_fixture.py --out tests/fixtures/ams2 --count 5` on Windows after `scripts/validate_ams2.py` passes to create raw AMS2 `.bin` fixture candidates with JSON sidecars. Use `uv run python scripts/capture_acc_fixture.py --out tests/fixtures/acc --count 5` on Windows with ACC running to create raw physics/graphics/static `.bin` page fixture candidates with JSON sidecars. Commit only captures that have been reviewed for provenance and are useful as stable regression evidence.

When raw sidecars are committed under `tests/fixtures/ams2/` or `tests/fixtures/acc/`, `tests/test_raw_fixtures.py` parses the corresponding `.bin` files in CI. The tests intentionally skip while no live captures exist.
