"""
Tests that the cross chain denom functions work as expected.
"""

from src.util import denom_info, denom_info_on_chain, DISCOVERY_CONCURRENCY_FACTOR
from tests.test_scheduler import ctx
import aiohttp
import pytest

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.asyncio
async def test_denom_info() -> None:
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        local_ctx = ctx(session)

        info = await denom_info("neutron-1", "untrn", session, local_ctx.endpoints)

        assert len(info) > 0


@pytest.mark.asyncio
async def test_denom_info() -> None:
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        local_ctx = ctx(session)

        info = await denom_info_on_chain(
            "neutron-1", "untrn", "osmosis-1", session, local_ctx.endpoints
        )

        assert info is not None
