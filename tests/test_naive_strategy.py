"""
Tests the naive strategy.
"""

from src.strategies.naive import (
    fmt_route_leg,
    get_routes_with_depth_limit_bfs,
    route_base_denom_profit,
)
from src.scheduler import Scheduler
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.contracts.pool.astroport import AstroportPoolDirectory
from src.contracts.auction import AuctionDirectory
from src.util import deployments
from tests.test_scheduler import ctx, strategy


def test_fmt_route_leg() -> None:
    """
    Test that the utility function for formatting a route leg behaves as
    expected.
    """

    # Register Osmosis and Astroport providers
    osmosis = OsmosisPoolDirectory()
    pool = list(list(osmosis.pools().values())[0].values())[0]

    # Check that a route leg can be formatter
    assert fmt_route_leg(pool) == "osmosis"

    # Check that astroport legs can be formatted
    astro = AstroportPoolDirectory(deployments())
    astro_pool = list(list(astro.pools().values())[0].values())[0]

    assert fmt_route_leg(astro_pool) == "astroport"

    # Check that valence auctions can be formatted
    valence = AuctionDirectory(deployments())
    auction = list(list(valence.auctions().values())[0].values())[0]

    assert fmt_route_leg(auction) == "valence"


def test_get_routes_with_depth_limit_bfs() -> None:
    """
    Tests the route graph explorer in a minimal and maximal case:
    - depth: 5, limit: 50
    - depth: 3, limit: 5
    """

    sched = Scheduler(ctx(), strategy)

    osmosis = OsmosisPoolDirectory()
    astro = AstroportPoolDirectory(deployments())

    osmo_pools = osmosis.pools()
    astro_pools = astro.pools()

    for osmo_base in osmo_pools.values():
        for osmo_pool in osmo_base.values():
            sched.register_provider(osmo_pool)

    for astro_base in astro_pools.values():
        for astro_pool in astro_base.values():
            sched.register_provider(astro_pool)

    providers = sched.providers
    auctions = sched.auctions

    routes_max = get_routes_with_depth_limit_bfs(
        5,
        50,
        "ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81",
        providers,
        auctions,
    )
    routes_min = get_routes_with_depth_limit_bfs(
        3,
        5,
        "ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81",
        providers,
        auctions,
    )

    assert len(routes_max) > 0
    assert len(routes_min) > 0


def test_route_base_denom_profit() -> None:
    """
    Tests the route profit calculator against USDC.
    """

    sched = Scheduler(ctx(), strategy)

    osmosis = OsmosisPoolDirectory()
    astro = AstroportPoolDirectory(deployments())

    osmo_pools = osmosis.pools()
    astro_pools = astro.pools()

    for osmo_base in osmo_pools.values():
        for osmo_pool in osmo_base.values():
            sched.register_provider(osmo_pool)

    for astro_base in astro_pools.values():
        for astro_pool in astro_base.values():
            sched.register_provider(astro_pool)

    providers = sched.providers
    auctions = sched.auctions

    routes = get_routes_with_depth_limit_bfs(
        3,
        5,
        "ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81",
        providers,
        auctions,
    )

    # Check that all routes can calculate profit successfully
    for route in routes:
        route_base_denom_profit(
            "ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81",
            10000,
            route,
        )
