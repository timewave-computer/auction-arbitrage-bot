"""
Tests that the scheduler works as expected.
"""

from src.scheduler import Scheduler, Ctx
from src.util import DISCOVERY_CONCURRENCY_FACTOR
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.contracts.pool.astroport import NeutronAstroportPoolDirectory
from src.contracts.pool.provider import PoolProvider
from src.contracts.auction import AuctionProvider
from tests.util import deployments, ctx, State
import aiohttp
import pytest
import grpc

pytest_plugins = ("pytest_asyncio",)


async def strategy(
    strat_ctx: Ctx[State],
    _pools: dict[str, dict[str, list[PoolProvider]]],
    _auctions: dict[str, dict[str, AuctionProvider]],
) -> Ctx[State]:
    """
    Noop strategy.
    """

    return strat_ctx


@pytest.mark.asyncio
async def test_init() -> None:
    """
    Test that a scheduler can be instantiated.
    """

    async with ctx() as test_ctx:
        sched = Scheduler(test_ctx, strategy)
        assert sched is not None


@pytest.mark.asyncio
async def test_register_provider() -> None:
    """
    Test that a provider can be registered to a scheduler.
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        osmosis = OsmosisPoolDirectory(deployments(), session)
        pool = list(list((await osmosis.pools()).values())[0].values())[0]

        async with ctx() as test_ctx:
            sched = Scheduler(test_ctx, strategy)

            directory = OsmosisPoolDirectory(deployments(), session)
            pools = await directory.pools()

            for base in pools.values():
                for pool in base.values():
                    sched.register_provider(pool)

            assert len(sched.providers) > 0


@pytest.mark.asyncio
async def test_poll() -> None:
    """
    Test that a strategy function can be run.
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        osmosis = OsmosisPoolDirectory(deployments(), session)
        astroport = NeutronAstroportPoolDirectory(
            deployments(),
            "neutron-1",
            session,
            [
                grpc.aio.secure_channel(
                    "neutron-grpc.publicnode.com:443",
                    grpc.ssl_channel_credentials(),
                )
            ],
        )

        async def simple_strategy(
            strat_ctx: Ctx[State],
            pools: dict[str, dict[str, list[PoolProvider]]],
            auctions: dict[str, dict[str, AuctionProvider]],
        ) -> Ctx[State]:
            assert len(pools) > 0
            assert len(auctions) > 0

            return strat_ctx

        async with ctx() as test_ctx:
            sched = Scheduler(test_ctx, simple_strategy)

            await sched.register_auctions()
            osmos_pools = await osmosis.pools()
            astro_pools = await astroport.pools()

            for base in osmos_pools.values():
                for pool in base.values():
                    sched.register_provider(pool)

            for astro_base in astro_pools.values():
                for astro_pool in astro_base.values():
                    sched.register_provider(astro_pool)

            await sched.poll()
