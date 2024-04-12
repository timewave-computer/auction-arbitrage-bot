from src.contracts.auction import AuctionProvider
from src.contracts.auction import (
    AuctionDirectory,
)
from src.util import deployments
import json
import pytest


def test_auctions():
    """
    Test that an auction directory can be created,
    and that it has some auctions.
    """

    auctions = AuctionDirectory(deployments())
    assert len(auctions.auctions()) > 0


def test_auction_provider():
    """
    Test that an auction can be queried.
    """

    auctions = AuctionDirectory(deployments()).auctions()

    for auction_base in auctions.values():
        for auction in auction_base.values():
            assert len(auction.asset_a()) != ""
            assert len(auction.asset_b()) != ""

            price = auction.exchange_rate()
            assert price >= 0
