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


def test_osmosis_poolfile() -> None:
    """
    Test that an Osmosis pool provider can be instantiated from
    a list of pools.
    """

    osmosis = OsmosisPoolDirectory(poolfile_path="tests/test_poolfile.json")
    pools = osmosis.pools()

    assert len([pair for base in pools.values() for pair in base.values()]) == 4

    for base in pools.values():
        for pool in base.values():
            assert isinstance(pool, OsmosisPoolProvider)


def test_astroport_poolfile() -> None:
    """
    Test that an Osmosis pool provider can be instantiated from
    a list of pools.
    """

    astro = NeutronAstroportPoolDirectory(
        deployments(), poolfile_path="tests/test_poolfile.json"
    )
    pools = astro.pools()

    assert len([pair for base in pools.values() for pair in base.values()]) == 4

    for base in pools.values():
        for pool in base.values():
            assert isinstance(pool, NeutronAstroportPoolProvider)


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


def test_osmosis_dump() -> None:
    """
    Tests that the an Osmosis PoolDirectory can provide a list of JSON pools,
    and that a pool can be dumped to JSON.
    """

    osmosis = OsmosisPoolDirectory()
    pools = osmosis.pools()

    # The directory must be able to dump pools
    assert len(osmosis.dump_pools(pools)) * 2 == len(
        [pair for base in pools.values() for pair in base.values()]
    )

    # All pools must be dumpable
    for base in pools.values():
        for pool in base.values():
            dump = pool.dump()

            keys = ["asset_a", "asset_b", "pool_id"]
            assert all([k in dump for k in keys])


def test_astroport_dump() -> None:
    """
    Tests that the an Astroport PoolDirectory can provide a list of JSON pools,
    and that a pool can be dumped to JSON.
    """

    astro = NeutronAstroportPoolDirectory(deployments())
    pools = astro.pools()

    # The directory must be able to dump pools
    assert len(astro.dump_pools(pools)) * 2 == len(
        [pair for base in pools.values() for pair in base.values()]
    )

    # All pools must be dumpable
    for base in pools.values():
        for pool in base.values():
            dump = pool.dump()

            keys = ["asset_a", "asset_b", "address"]
            assert all([k in dump for k in keys])
