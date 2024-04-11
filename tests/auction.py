from src.contracts.auction import AuctionProvider
from src.contracts.auction import (
    AuctionDirectory,
)
from tests.util import deployments
import json
import pytest


def test_auctions():
    """
    Test that an auction directory can be created,
    and that it has some auctions.
    """

    auctions = AuctionDirectory(deployments())
    assert len(auctions.auctions()) != 0
