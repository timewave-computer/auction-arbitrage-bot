"""
Tests that the auction directory and providers work as expected.
"""

from src.contracts.auction import AuctionDirectory, AuctionProvider
from src.util import deployments


def test_auctions() -> None:
    """
    Test that an auction directory can be created,
    and that it has some auctions.
    """

    auctions = AuctionDirectory(deployments())
    assert len(auctions.auctions()) > 0


def test_auction_provider() -> None:
    """
    Test that an auction can be queried.
    """

    auctions = AuctionDirectory(deployments()).auctions()

    for auction_base in auctions.values():
        for auction in auction_base.values():
            assert len(auction.asset_a()) != 0
            assert len(auction.asset_b()) != 0

            price = auction.exchange_rate()
            assert price >= 0


def test_auctions_poolfile() -> None:
    """
    Tests that auctions can be loaded from a poolfile.
    """

    auctions = AuctionDirectory(
        deployments(), poolfile_path="tests/test_poolfile.json"
    ).auctions()

    assert len([pair for base in auctions.values() for pair in base.values()]) == 2

    for base in auctions.values():
        for pool in base.values():
            assert isinstance(pool, AuctionProvider)
