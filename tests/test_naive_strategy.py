"""
Tests the naive strategy.
"""

from src.strategies.naive import fmt_route_leg
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.contracts.pool.astroport import AstroportPoolDirectory
from src.contracts.auction import AuctionDirectory
from src.util import deployments


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
