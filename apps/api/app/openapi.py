"""`python -m app.openapi`: print the OpenAPI document (`make gen` turns it into TS types).

Builds the app with throwaway settings so it never needs a database or secrets.
"""

from __future__ import annotations

import json
import sys

from app.main import create_app
from app.settings import Settings


def document() -> dict[str, object]:
    settings = Settings(  # throwaway values: nothing connects during schema generation
        _env_file=None,
        database_url="postgresql+asyncpg://x:x@localhost/x",
        redis_url="redis://localhost:6379/0",
        session_secret="openapi-generation-only-0123456789",
        token_hash_pepper="openapi-generation-only-0123456789",
    )
    return create_app(settings).openapi()


if __name__ == "__main__":
    json.dump(document(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
