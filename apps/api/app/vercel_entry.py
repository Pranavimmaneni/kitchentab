"""Vercel Services entrypoint for the API (ADR-0011): `app.vercel_entry:app`.

Settings come from the project's environment variables and are validated at import, so a missing
secret fails the deployment's first request loudly instead of serving a half-configured app.
Never runs migrations (VERCEL-06): `make deploy` runs Alembic against the direct URL.
"""

from __future__ import annotations

from app.main import create_app

app = create_app()
