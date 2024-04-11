from src.contracts.pool.provider import PoolProvider
from src.contracts.pool.astroport import (
    AstroportPoolDirectory,
    AstroportPoolProvider,
    token_to_addr,
)
from src.contracts.pool.osmosis import OsmosisPoolDirectory, OsmosisPoolProvider
from src.util import deployments
import pytest


def test_astroport_pools():
    """
    Test that an astroport poool provider can be instantiated,
    and that it has some pools.
    """

    astroport = AstroportPoolDirectory(deployments())
    pools = astroport.pools()

    assert len(pools) != 0

    for base in pools.values():
        for pool in base.values():
            assert isinstance(pool, AstroportPoolProvider)


def test_osmosis_pools():
    """
    Test that an osmosis poool provider can be instantiated,
    and that it has some pools.
    """

    osmosis = OsmosisPoolDirectory()
    pools = osmosis.pools()

    assert len(pools) != 0

    for base in pools.values():
        for pool in base.values():
            assert isinstance(pool, OsmosisPoolProvider)


def test_astroport_provider():
    """
    Test that an astroport poool can be queried
    for simulation information and basic information
    """

    astroport = AstroportPoolDirectory(deployments())
    pool = list(list(astroport.pools().values())[0].values())[0]

    assert len(pool.asset_a()) != ""
    assert len(pool.asset_b()) != ""

    assert pool.simulate_swap_asset_a(1000) >= 0
    assert pool.simulate_swap_asset_b(1000) >= 0


def test_osmosis_provider():
    """
    Test that an osmosis poool can be queried
    for simulation information and basic information
    """

    osmosis = OsmosisPoolDirectory()
    pool = list(list(osmosis.pools().values())[0].values())[0]

    assert len(pool.asset_a()) != ""
    assert len(pool.asset_b()) != ""

    assert pool.simulate_swap_asset_a(1000) >= 0
    assert pool.simulate_swap_asset_b(1000) >= 0
