"""
Tests that the osmosis and astroport pools work as expected.
"""

from src.contracts.pool.astroport import (
    NeutronAstroportPoolDirectory,
    NeutronAstroportPoolProvider,
)
from src.contracts.pool.osmosis import OsmosisPoolDirectory, OsmosisPoolProvider
from src.util import DISCOVERY_CONCURRENCY_FACTOR
from tests.util import deployments
import pytest
import aiohttp
import grpc


pytest_plugins = ("pytest_asyncio",)


@pytest.mark.asyncio
async def test_astroport_pools() -> None:
    """
    Test that an astroport poool provider can be instantiated,
    and that it has some pools.
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
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
        pools = await astroport.pools()

        assert len(pools) != 0

        for base in pools.values():
            for pool in base.values():
                assert isinstance(pool, NeutronAstroportPoolProvider)


@pytest.mark.asyncio
async def test_osmosis_pools() -> None:
    """
    Test that an osmosis poool provider can be instantiated,
    and that it has some pools.
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        osmosis = OsmosisPoolDirectory(deployments(), session)
        pools = await osmosis.pools()

        assert len(pools) != 0

        for base in pools.values():
            for pool in base.values():
                assert isinstance(pool, OsmosisPoolProvider)


@pytest.mark.asyncio
async def test_astroport_provider() -> None:
    """
    Test that an astroport poool can be queried
    for simulation information and basic information
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
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
        pools = await astroport.pools()

        # All pools must have assets
        assert all(
            map(
                lambda base: all(map(lambda pool: pool.asset_a() != "", base.values())),
                pools.values(),
            )
        )
        assert all(
            map(
                lambda base: all(map(lambda pool: pool.asset_b() != "", base.values())),
                pools.values(),
            )
        )

        for base in pools.values():
            for pool in base.values():
                if (await pool.balance_asset_a()) <= 0 or (
                    await pool.balance_asset_b()
                ) <= 0:
                    continue

                in_a = await pool.simulate_swap_asset_a(10)
                assert in_a >= 0

                in_b = await pool.simulate_swap_asset_b(10)
                assert in_b >= 0

                assert (await pool.reverse_simulate_swap_asset_a(in_a)) >= 0
                assert (await pool.reverse_simulate_swap_asset_b(in_b)) >= 0

                break
            break


@pytest.mark.asyncio
async def test_osmosis_provider() -> None:
    """
    Test that an osmosis poool can be queried
    for simulation information and basic information
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        osmosis = OsmosisPoolDirectory(deployments(), session)
        pools = await osmosis.pools()

        # All pools must have assets
        assert all(
            map(
                lambda base: all(map(lambda pool: pool.asset_a() != "", base.values())),
                pools.values(),
            )
        )
        assert all(
            map(
                lambda base: all(map(lambda pool: pool.asset_b() != "", base.values())),
                pools.values(),
            )
        )

        # At least one pool must have some assets to swap
        await list(list(pools.values())[0].values())[0].simulate_swap_asset_a(1000)

        for base in pools.values():
            for pool in base.values():
                if (await pool.balance_asset_a()) <= 0 or (
                    await pool.balance_asset_b()
                ) <= 0:
                    continue

                in_a = await pool.simulate_swap_asset_a(10)
                assert in_a >= 0

                in_b = await pool.simulate_swap_asset_b(10)
                assert in_b >= 0

                assert (await pool.reverse_simulate_swap_asset_a(in_a)) >= 0
                assert (await pool.reverse_simulate_swap_asset_b(in_b)) >= 0

                break
            break


@pytest.mark.asyncio
async def test_osmosis_dump() -> None:
    """
    Tests that the an Osmosis PoolDirectory can provide a list of JSON pools,
    and that a pool can be dumped to JSON.
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        osmosis = OsmosisPoolDirectory(deployments(), session)
        pools = await osmosis.pools()

        # The directory must be able to dump pools
        assert len(osmosis.dump_pools(pools)) * 2 == len(
            [pair for base in pools.values() for pair in base.values()]
        )

        # All pools must be dumpable
        for base in pools.values():
            for pool in base.values():
                dump = pool.dump()

                keys = ["asset_a", "asset_b", "pool_id"]
                assert all((k in dump for k in keys))


@pytest.mark.asyncio
async def test_astroport_dump() -> None:
    """
    Tests that the an Astroport PoolDirectory can provide a list of JSON pools,
    and that a pool can be dumped to JSON.
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        astro = NeutronAstroportPoolDirectory(
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
        pools = await astro.pools()

        # The directory must be able to dump pools
        assert len(astro.dump_pools(pools)) * 2 == len(
            [pair for base in pools.values() for pair in base.values()]
        )

        # All pools must be dumpable
        for base in pools.values():
            for pool in base.values():
                dump = pool.dump()

                keys = ["asset_a", "asset_b", "address"]
                assert all((k in dump for k in keys))
