"""
Tests that the auction directory and providers work as expected.
"""

from src.contracts.auction import AuctionDirectory, AuctionProvider
from src.util import deployments, DISCOVERY_CONCURRENCY_FACTOR
import pytest
import aiohttp
import grpc

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.asyncio
async def test_auctions() -> None:
    """
    Test that an auction directory can be created,
    and that it has some auctions.
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        auctions = AuctionDirectory(
            deployments(),
            session,
            [
                grpc.aio.secure_channel(
                    "neutron-grpc.publicnode.com:443",
                    grpc.ssl_channel_credentials(),
                )
            ],
        )
        assert len(await auctions.auctions()) > 0


@pytest.mark.asyncio
async def test_auction_provider() -> None:
    """
    Test that an auction can be queried.
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        auctions = await AuctionDirectory(
            deployments(),
            session,
            [
                grpc.aio.secure_channel(
                    "neutron-grpc.publicnode.com:443",
                    grpc.ssl_channel_credentials(),
                )
            ],
        ).auctions()

        for auction_base in auctions.values():
            for auction in auction_base.values():
                assert len(auction.asset_a()) != 0
                assert len(auction.asset_b()) != 0

                price = await auction.exchange_rate()
                assert price >= 0


@pytest.mark.asyncio
async def test_auctions_poolfile() -> None:
    """
    Tests that auctions can be loaded from a poolfile.
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        auctions = await AuctionDirectory(
            deployments(),
            session,
            [
                grpc.aio.secure_channel(
                    "neutron-grpc.publicnode.com:443",
                    grpc.ssl_channel_credentials(),
                )
            ],
            poolfile_path="tests/test_poolfile.json",
        ).auctions()

        assert len([pair for base in auctions.values() for pair in base.values()]) == 1

        for base in auctions.values():
            for pool in base.values():
                assert isinstance(pool, AuctionProvider)
