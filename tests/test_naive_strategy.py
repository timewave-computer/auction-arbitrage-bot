"""
Tests the naive strategy.
"""

from src.strategies.util import fmt_route_leg
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.contracts.route import Leg
from src.contracts.pool.astroport import NeutronAstroportPoolDirectory
from src.contracts.auction import AuctionDirectory
from src.util import deployments, DISCOVERY_CONCURRENCY_FACTOR
import pytest
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
