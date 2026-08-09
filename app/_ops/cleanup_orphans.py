"""Quick DB housekeeping helper (not part of the app runtime).

Used during the FU-* deploy + enrichment work to wipe orphan rows
(token_id = 0) the first seed run left behind. Kept under
app/_ops/ so the test suite and runtime never import it.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.db.session import AsyncSessionLocal


async def main() -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("DELETE FROM agent_cache WHERE token_id = 0")
        )
        await session.commit()
        print(f"deleted {result.rowcount} row(s) with token_id = 0")

        count = await session.execute(text("SELECT count(*) FROM agent_cache"))
        print(f"agent_cache total: {count.scalar()}")


if __name__ == "__main__":
    asyncio.run(main())
