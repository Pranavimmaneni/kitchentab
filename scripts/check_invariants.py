"""Invariant checker (TEST_PLAN DATA-05). Usage from the repo root:

DATABASE_URL=postgresql+asyncpg://... uv run --project apps/api python scripts/check_invariants.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.tools.check_invariants import main

if __name__ == "__main__":
    sys.exit(main())
