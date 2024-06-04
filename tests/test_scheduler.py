"""
Tests that the scheduler works as expected.
"""

from dataclasses import dataclass
from typing import List
from cosmpy.aerial.client import LedgerClient, NetworkConfig
from cosmpy.aerial.wallet import LocalWallet
from src.scheduler import Scheduler, Ctx
from src.util import (
    deployments,
    NEUTRON_NETWORK_CONFIG,
    DISCOVERY_CONCURRENCY_FACTOR,
    custom_neutron_network_config,
)
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.contracts.pool.astroport import NeutronAstroportPoolDirectory
from src.contracts.pool.provider import PoolProvider
from src.contracts.auction import AuctionProvider
import aiohttp
import pytest
import grpc

pytest_plugins = ("pytest_asyncio",)

# Note: this account has no funds and is not used for anything
TEST_WALLET_MNEMONIC = (
    "update armed valve web gate shiver birth exclude curtain cotton juice property"
)


@dataclass
class State:
    balance: int


async def strategy(
    strat_ctx: Ctx[State],
    _pools: dict[str, dict[str, List[PoolProvider]]],
    _auctions: dict[str, dict[str, AuctionProvider]],
) -> Ctx[State]:
    """
    Noop strategy.
    """

    return strat_ctx


def ctx(session: aiohttp.ClientSession) -> Ctx[State]:
    """
    Gets a default context for test schedulers.
    """

    endpoints: dict[str, dict[str, list[str]]] = {
        "neutron": {
            "http": ["https://neutron-rest.publicnode.com"],
            "grpc": ["grpc+https://neutron-grpc.publicnode.com:443"],
        },
        "osmosis": {
            "http": ["https://lcd.osmosis.zone"],
            "grpc": ["grpc+https://osmosis-grpc.publicnode.com:443"],
        },
    }

    return Ctx(
        {
            "neutron": [
                LedgerClient(NEUTRON_NETWORK_CONFIG),
                *[
                    LedgerClient(custom_neutron_network_config(endpoint))
                    for endpoint in endpoints["neutron"]["grpc"]
                ],
            ],
            "osmosis": [
                *[
                    LedgerClient(
                        NetworkConfig(
                            chain_id="osmosis-1",
                            url=endpoint,
                            fee_minimum_gas_price=0.0053,
                            fee_denomination="uosmo",
                            staking_denomination="uosmo",
                        )
                    )
                    for endpoint in endpoints["osmosis"]["grpc"]
                ],
            ],
        },
        endpoints,
        LocalWallet.from_mnemonic(TEST_WALLET_MNEMONIC, prefix="neutron"),
        {
            "pool_file": None,
            "poll_interval": 120,
            "discovery_interval": 600,
            "hops": 3,
            "pools": 100,
            "require_leg_types": set(),
            "base_denom": "",
            "profit_margin": 100,
            "wallet_mnemonic": "",
            "cmd": "",
            "net_config": "",
            "log_file": "",
            "history_file": "",
            "skip_api_key": None,
        },
        None,
        False,
        session,
        [],
    ).with_state(State(1000))


@pytest.mark.asyncio
async def test_init() -> None:
    """
    Test that a scheduler can be instantiated.
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        sched = Scheduler(ctx(session), strategy)
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
        osmosis = OsmosisPoolDirectory(session)
        pool = list(list((await osmosis.pools()).values())[0].values())[0]

        sched = Scheduler(ctx(session), strategy)

        directory = OsmosisPoolDirectory(session)
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
        osmosis = OsmosisPoolDirectory(session)
        astroport = NeutronAstroportPoolDirectory(
            deployments(),
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
            pools: dict[str, dict[str, List[PoolProvider]]],
            auctions: dict[str, dict[str, AuctionProvider]],
        ) -> Ctx[State]:
            assert len(pools) > 0
            assert len(auctions) > 0

            return strat_ctx

        sched = Scheduler(ctx(session), simple_strategy)

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
