"""Synchronize the offline decision-log fallback without changing the rest of the page."""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from uuid import uuid4


def synchronize(*, check: bool = False) -> bool:
    root = Path(__file__).resolve().parents[1]
    target = root / "docs" / "index.html"
    original = target.read_bytes()
    decisions = (root / "docs" / "DECISIONS.md").read_text(encoding="utf8").strip()
    if "</script" in decisions.lower():
        raise ValueError("Decision log contains a script closing tag; review the embedding format")
    matches = list(re.finditer(
        rb'(<script type="text/plain" id="decisionsSource">)(.*?)(</script>)',
        original, re.DOTALL))
    if len(matches) != 1:
        raise ValueError("Expected exactly one decisionsSource fallback")
    match = matches[0]
    current = match.group(2).decode("utf8").replace("\r\n", "\n").strip()
    if current == decisions:
        return True
    if check:
        return False
    replacement = original[:match.start(2)] + ("\n" + decisions + "\n").encode("utf8")
    replacement += original[match.end(2):]
    temporary = target.parent / (".index-" + uuid4().hex + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(replacement)
        stream.flush()
        os.fsync(stream.fileno())
    # Refuse concurrent edits detected while the derived content was prepared.
    if target.read_bytes() != original:
        raise RuntimeError(f"Index changed during synchronization; preserved {temporary.name}")
    temporary.replace(target)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Report drift without editing the page"
    )
    args = parser.parse_args()
    current = synchronize(check=args.check)
    print("PASS: decision fallback synchronized" if current else "FAIL: decision fallback is stale")
    raise SystemExit(0 if current else 1)
