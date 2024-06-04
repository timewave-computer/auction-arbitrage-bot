"""
Tests the naive strategy.
"""

from src.strategies.naive import (
    fmt_route_leg,
    listen_routes_with_depth_dfs,
)
from src.scheduler import Scheduler
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.contracts.route import Leg
from src.contracts.pool.astroport import NeutronAstroportPoolDirectory
from src.contracts.auction import AuctionDirectory
from src.util import deployments, DISCOVERY_CONCURRENCY_FACTOR
from tests.test_scheduler import ctx, strategy
import pytest
import asyncio
import aiohttp
import grpc


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
        pool = list(list((await osmosis.pools()).values())[0].values())[0]

        # Check that a route leg can be formatter
        assert fmt_route_leg(Leg(pool.asset_a, pool.asset_b, pool)) == "osmosis"

        # Check that astroport legs can be formatted
        astro = NeutronAstroportPoolDirectory(
            deployments(),
            session,
            [
                grpc.aio.secure_channel(
                    "neutron-grpc.publicnode.com:443",
                    grpc.ssl_channel_credentials(),
                )
            ],
        )
        astro_pool = list(list((await astro.pools()).values())[0].values())[0]

        assert (
            fmt_route_leg(Leg(astro_pool.asset_a, astro_pool.asset_b, astro_pool))
            == "astroport"
        )

        # Check that valence auctions can be formatted
        valence = AuctionDirectory(
            deployments(),
            session,
            [
                grpc.aio.secure_channel(
                    "neutron-grpc.publicnode.com:443",
                    grpc.ssl_channel_credentials(),
                )
            ],
        )
        auction = list(list((await valence.auctions()).values())[0].values())[0]

        assert (
            fmt_route_leg(Leg(auction.asset_a, auction.asset_b, auction)) == "auction"
        )


@pytest.mark.asyncio
async def test_get_routes_with_depth_limit_dfs() -> None:
    """
    Tests the route graph explorer in a minimal and maximal case:
    - depth: 5, limit: 50
    - depth: 3, limit: 5
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        sched = Scheduler(ctx(session), strategy)

        osmosis = OsmosisPoolDirectory(session)
        astro = NeutronAstroportPoolDirectory(
            deployments(),
            session,
            [
                grpc.aio.secure_channel(
                    "neutron-grpc.publicnode.com:443",
                    grpc.ssl_channel_credentials(),
                )
            ],
        )

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
            set(),
            providers,
            auctions,
            sched.ctx,
        ):
            break

        async for r, route in listen_routes_with_depth_dfs(
            5,
            "ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81",
            set(),
            providers,
            auctions,
            sched.ctx,
        ):
            break
