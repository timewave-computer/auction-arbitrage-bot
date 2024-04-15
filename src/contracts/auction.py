"""
Implements a wrapper around a valence auction, providing pricing information
for the auction.
"""

from typing import Any
from cosmpy.aerial.contract import LedgerContract  # type: ignore
from cosmpy.aerial.client import LedgerClient  # type: ignore
from src.util import NEUTRON_NETWORK_CONFIG, WithContract


class AuctionProvider(WithContract):
    """
    Provides pricing and asset information for an arbitrary auction on valenece.
    """

    def __init__(
        self,
        contract_info: tuple[dict[str, Any], LedgerClient, str],
        asset_a: str,
        asset_b: str,
    ):
        WithContract.__init__(
            self, contract_info[0], contract_info[1], contract_info[2], "auction"
        )
        self.asset_a_denom = asset_a
        self.asset_b_denom = asset_b

    def exchange_rate(self) -> float:
        """
        Gets the number of asset_b required to purchase a single asset_a.
        """

        auction_info = self.contract.query("get_auction")

        # No swap is possible since the auction is closed
        if auction_info["status"] != "started":
            return 0

        # Calculate prices manually by following the
        # pricing curve to the given block
        current_block_height = self.client.query_height()

        start_price = float(auction_info["start_price"])
        end_price = float(auction_info["end_price"])
        start_block = float(auction_info["start_block"])
        end_block = float(auction_info["end_block"])

        # The change in price per block
        price_delta_per_block: float = (start_price - end_price) / (
            end_block - start_block
        )

        # The current price
        current_price: float = start_price - (
            price_delta_per_block * (current_block_height - start_block)
        )

        return current_price

    def asset_a(self) -> str:
        """
        Gets the asset being sold in the pool.
        """

        return self.asset_a_denom

    def asset_b(self) -> str:
        """
        Gets the asset being used to purchase in the pool.
        """

        return self.asset_b_denom

    def remaining_asset_a(self) -> float:
        """
        Gets the amount of the asking asset left in the auction.
        """

        return float(self.contract.query("get_auction")["available_amount"])

    def __hash__(self) -> int:
        return hash(self.address)


class AuctionDirectory:
    """
    A wrapper around an auction manager providing:
    - Accessors for all auctions on valence
    - AuctionProviders for each auction
    """

    def __init__(self, deployments: dict[str, Any]):
        self.client = LedgerClient(NEUTRON_NETWORK_CONFIG)
        self.deployment_info = deployments["auctions"]["neutron"]

        deployment_info = self.deployment_info["auctions_manager"]
        self.directory_contract = LedgerContract(
            deployment_info["src"], self.client, address=deployment_info["address"]
        )

    def auctions(self) -> dict[str, dict[str, AuctionProvider]]:
        """ "
        Gets an AuctionProvider for every pair on valence.
        """

        auction_infos = self.directory_contract.query(
            {"get_pairs": {"start_after": None, "limit": None}}
        )
        auctions: dict[str, dict[str, AuctionProvider]] = {}

        for auction in auction_infos:
            pair, addr = auction
            asset_a, asset_b = pair

            provider = AuctionProvider(
                (self.deployment_info, self.client, addr),
                asset_a,
                asset_b,
            )

            if asset_a not in auctions:
                auctions[asset_a] = {}

            auctions[asset_a][asset_b] = provider

        return auctions

    def contract(self) -> LedgerContract:
        """
        Gets the conract backing the auction directory.
        """

        return self.directory_contract
