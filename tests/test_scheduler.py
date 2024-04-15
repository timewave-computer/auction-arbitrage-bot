"""
Tests that the scheduler works as expected.
"""

from typing import List
from cosmpy.aerial.client import LedgerClient  # type: ignore
from src.scheduler import Scheduler, Ctx
from src.util import deployments, NEUTRON_NETWORK_CONFIG
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.contracts.pool.astroport import NeutronAstroportPoolDirectory
from src.contracts.pool.provider import PoolProvider
from src.contracts.auction import AuctionProvider


def strategy(
    strat_ctx: Ctx,
    _pools: dict[str, dict[str, List[PoolProvider]]],
    _auctions: dict[str, dict[str, AuctionProvider]],
) -> Ctx:
    """
    Noop strategy.
    """

    return strat_ctx


def ctx() -> Ctx:
    """
    Gets a default context for test schedulers.
    """

    return Ctx(
        LedgerClient(NEUTRON_NETWORK_CONFIG),
        {
            "poll_interval": 120,
            "discovery_interval": 1000,
            "max_hops": 5,
            "num_routes_considered": 20,
            "base_denom": "ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81",
            "profit_margin": 10,
            "wallet_address": "neutron1cm9ckhh8839tpwvpqqqsdvvra32z5p8w97trje",
        },
        None,
    )


def test_init() -> None:
    """
    Test that a scheduler can be instantiated.
    """

    sched = Scheduler(ctx(), strategy)
    assert sched is not None


def test_register_provider() -> None:
    """
    Test that a provider can be registered to a scheduler.
    """

    osmosis = OsmosisPoolDirectory()
    pool = list(list(osmosis.pools().values())[0].values())[0]

    sched = Scheduler(ctx(), strategy)

    directory = OsmosisPoolDirectory()
    pools = directory.pools()

    for base in pools.values():
        for pool in base.values():
            sched.register_provider(pool)

    assert len(sched.providers) > 0


def test_poll() -> None:
    """
    Test that a strategy function can be run.
    """

    osmosis = OsmosisPoolDirectory()
    astroport = NeutronAstroportPoolDirectory(deployments())

    def simple_strategy(
        strat_ctx: Ctx,
        pools: dict[str, dict[str, List[PoolProvider]]],
        auctions: dict[str, dict[str, AuctionProvider]],
    ) -> Ctx:
        assert len(pools) > 0
        assert len(auctions) > 0

        return strat_ctx

    sched = Scheduler(ctx(), simple_strategy)

    osmos_pools = osmosis.pools()
    astro_pools = astroport.pools()

    for base in osmos_pools.values():
        for pool in base.values():
            sched.register_provider(pool)

    for astro_base in astro_pools.values():
        for astro_pool in astro_base.values():
            sched.register_provider(astro_pool)

    sched.poll()
