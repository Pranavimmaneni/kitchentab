"""Receipt extraction accuracy report (BUILD_ALL C1, EXTRACT-03). What `make test-extract` runs.

    cd apps/api && uv run python ../../scripts/receipts/evaluate.py [fixtures_dir]

Writes packages/fixtures/receipts/REPORT.md and report.json; exits 1 if a target is missed.
The logic lives in apps/api/app/extraction/evaluate.py so pytest (EXTRACT-03) runs the same code.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from app.extraction.evaluate import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
