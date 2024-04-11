from src.scheduler import Scheduler
from src.util import deployments
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.contracts.pool.astroport import AstroportPoolDirectory
from src.contracts.pool.provider import PoolProvider
from src.contracts.auction import AuctionProvider
import json
import pytest
from typing import List


# Noop strategy
def strategy(
    pools: dict[str, dict[str, List[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
):
    return None


def test_init():
    """
    Test that a scheduler can be instantiated.
    """

    sched = Scheduler(strategy)
    assert sched is not None


def test_register_provider():
    """
    Test that a provider can be registered to a scheduler.
    """

    osmosis = OsmosisPoolDirectory()
    pool = list(list(osmosis.pools().values())[0].values())[0]

    sched = Scheduler(strategy)

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
        pools: dict[str, dict[str, List[PoolProvider]]],
        auctions: dict[str, dict[str, AuctionProvider]],
    ):
        assert len(pools) > 0
        assert len(auctions) > 0

    sched = Scheduler(simple_strategy)

    osmos_pools = osmosis.pools()
    astro_pools = astroport.pools()

    for base in osmos_pools.values():
        for pool in base.values():
            sched.register_provider(pool)

    for base in astro_pools.values():
        for pool in base.values():
            sched.register_provider(pool)

    sched.poll()
