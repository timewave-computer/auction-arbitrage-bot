"""
Tests the naive strategy.
"""

from src.strategies.naive import (
    fmt_route_leg,
    listen_routes_with_depth_dfs,
)
from src.scheduler import Scheduler
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.contracts.pool.astroport import NeutronAstroportPoolDirectory
from src.contracts.auction import AuctionDirectory
from src.util import deployments, DISCOVERY_CONCURRENCY_FACTOR
from tests.test_scheduler import ctx, strategy
import pytest
import asyncio
import aiohttp


pytest_plugins = ("pytest_asyncio",)


@pytest.mark.asyncio
async def test_fmt_route_leg() -> None:
    """
    Test that the utility function for formatting a route leg behaves as
    expected.
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        # Register Osmosis and Astroport providers
        osmosis = OsmosisPoolDirectory(session)
        pool = list(list(osmosis.pools().values())[0].values())[0]

        # Check that a route leg can be formatter
        assert fmt_route_leg(pool) == "osmosis"

        # Check that astroport legs can be formatted
        astro = NeutronAstroportPoolDirectory(deployments())
        astro_pool = list(list(astro.pools().values())[0].values())[0]

        assert fmt_route_leg(astro_pool) == "astroport"

        # Check that valence auctions can be formatted
        valence = AuctionDirectory(deployments())
        auction = list(list(valence.auctions().values())[0].values())[0]

        assert fmt_route_leg(auction) == "valence"


@pytest.mark.asyncio
async def test_get_routes_with_depth_limit_dfs() -> None:
    """
    Tests the route graph explorer in a minimal and maximal case:
    - depth: 5, limit: 50
    - depth: 3, limit: 5
    """

    sched = Scheduler(ctx(), strategy)

    osmosis = OsmosisPoolDirectory()
    astro = NeutronAstroportPoolDirectory(deployments())

    osmo_pools = await osmosis.pools()
    astro_pools = await astro.pools()

    for osmo_base in osmo_pools.values():
        for osmo_pool in osmo_base.values():
            sched.register_provider(osmo_pool)

    for astro_base in astro_pools.values():
        for astro_pool in astro_base.values():
            sched.register_provider(astro_pool)

    providers = sched.providers
    auctions = sched.auctions

    async for r, route in listen_routes_with_depth_dfs(
        3,
        "ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81",
        providers,
        auctions,
        sched.ctx,
    ):
        break

    async for r, route in listen_routes_with_depth_dfs(
        5,
        "ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81",
        providers,
        auctions,
        sched.ctx,
    ):
        break
