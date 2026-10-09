"""`python -m app.seed <scenario>`: reset the local database and load a scenario (`make seed`)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

from app.db import make_engine, make_sessionmaker
from app.links import bill_token, invite_token
from app.seed.scenario import load_scenario
from app.seed.writer import truncate_all, write_scenario
from app.settings import get_settings


async def run(name: str) -> int:
    settings = get_settings()
    if settings.is_deployed:
        print(f"refusing to seed in {settings.app_env}", file=sys.stderr)
        return 2
    scenario = load_scenario(name)
    engine = make_engine(settings.database_url, pool_size=1)
    try:
        async with make_sessionmaker(engine)() as session, session.begin():
            await truncate_all(session)
            ids = await write_scenario(session, scenario, settings.token_hash_pepper.get_secret_value())
    finally:
        await engine.dispose()
    house = ids[f"house:{scenario.name}"]
    print(f"seeded scenario {scenario.name}: house {house}, {len(scenario.bills)} bills, {len(ids)} ids")
    pepper = settings.token_hash_pepper.get_secret_value()
    created = datetime.fromisoformat(str(scenario.raw["house"]["created_at"]))
    print(f"invite link: {settings.public_link_base}/h/{invite_token(pepper, house, created)[0]}")
    for bill in scenario.bills:
        at = bill.sent_at or scenario.now
        token = bill_token(pepper, ids[f"bill:{bill.key}"], at)[0]
        print(f"bill link ({bill.store}): {settings.public_link_base}/b/{token}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", nargs="?", default="apt4b")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.scenario)))


if __name__ == "__main__":
    main()
