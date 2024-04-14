from src.scheduler import Scheduler, Ctx
from src.util import deployments, NEUTRON_NETWORK_CONFIG
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.contracts.pool.astroport import AstroportPoolDirectory
from src.contracts.pool.provider import PoolProvider
from src.contracts.auction import AuctionProvider
from cosmpy.aerial.client import LedgerClient  # type: ignore
import json
import pytest
from typing import List


# Noop strategy
def strategy(
    ctx: Ctx,
    pools: dict[str, dict[str, List[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
) -> Ctx:
    return ctx


def ctx():
    return Ctx(
        LedgerClient(NEUTRON_NETWORK_CONFIG),
        120,
        1000,
        5,
        20,
        10,
        "ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81",
        "neutron1cm9ckhh8839tpwvpqqqsdvvra32z5p8w97trje",
    )


def test_init():
    """
    Test that a scheduler can be instantiated.
    """

    sched = Scheduler(ctx(), strategy)
    assert sched is not None


def test_register_provider():
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


def test_poll():
    """
    Test that a strategy function can be run.
    """

    osmosis = OsmosisPoolDirectory()
    astroport = AstroportPoolDirectory(deployments())

    def simple_strategy(
        ctx: Ctx,
        pools: dict[str, dict[str, List[PoolProvider]]],
        auctions: dict[str, dict[str, AuctionProvider]],
    ) -> Ctx:
        assert len(pools) > 0
        assert len(auctions) > 0

    sched = Scheduler(ctx(), simple_strategy)

    osmos_pools = osmosis.pools()
    astro_pools = astroport.pools()

    for base in osmos_pools.values():
        for pool in base.values():
            sched.register_provider(pool)

    for base in astro_pools.values():
        for pool in base.values():
            sched.register_provider(pool)

    sched.poll()
