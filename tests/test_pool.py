"""
Tests that the osmosis and astroport pools work as expected.
"""

from src.contracts.pool.astroport import (
    NeutronAstroportPoolDirectory,
    NeutronAstroportPoolProvider,
)
from src.contracts.pool.osmosis import OsmosisPoolDirectory, OsmosisPoolProvider
from src.util import deployments


def test_astroport_pools() -> None:
    """
    Test that an astroport poool provider can be instantiated,
    and that it has some pools.
    """

    astroport = NeutronAstroportPoolDirectory(deployments())
    pools = astroport.pools()

    assert len(pools) != 0

    for base in pools.values():
        for pool in base.values():
            assert isinstance(pool, NeutronAstroportPoolProvider)


def test_osmosis_pools() -> None:
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


def test_astroport_provider() -> None:
    """
    Test that an astroport poool can be queried
    for simulation information and basic information
    """

    astroport = NeutronAstroportPoolDirectory(deployments())
    pools = astroport.pools()

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
    assert any(
        map(
            lambda base: any(
                map(lambda pool: pool.simulate_swap_asset_a(1000) >= 0, base.values())
            ),
            pools.values(),
        )
    )


def test_osmosis_provider() -> None:
    """
    Test that an osmosis poool can be queried
    for simulation information and basic information
    """

    osmosis = OsmosisPoolDirectory()
    pools = osmosis.pools()

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
    assert any(
        map(
            lambda base: any(
                map(lambda pool: pool.simulate_swap_asset_a(1000) >= 0, base.values())
            ),
            pools.values(),
        )
    )
