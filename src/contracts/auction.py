from src.util import NEUTRON_NETWORK_CONFIG
from cosmpy.aerial.contract import LedgerContract  # type: ignore
from cosmpy.aerial.client import LedgerClient  # type: ignore
from typing import Any
from functools import cached_property


class AuctionProvider:
    """
    Provides pricing and asset information for an arbitrary auction on valenece.
    """

    def __init__(
        self,
        deployment_info: dict[str, Any],
        client: LedgerClient,
        address: str,
        asset_a: str,
        asset_b: str,
    ):
        self.deployment_info = deployment_info
        self.client = client
        self.address = address
        self.asset_a_denom = asset_a
        self.asset_b_denom = asset_b

    @cached_property
    def contract(self) -> LedgerContract:
        return LedgerContract(
            self.deployment_info["auction"]["src"], self.client, address=self.address
        )

    def exchange_rate(self) -> float:
        auction_info = self.contract.query("get_auction")

        # No swap is possible since the auction is closed
        if auction_info["status"] != "started":
            return 0

        # Calcualte prices manually by following the
        # pricing curve to the given block
        current_block_height = self.client.query_height()

        # The change in price per block
        price_delta_per_block = (
            float(auction_info["start_price"]) - float(auction_info["end_price"])
        ) / (float(auction_info["end_block"]) - float(auction_info["start_block"]))
        current_price = float(auction_info["start_price"]) - (
            price_delta_per_block
            * (current_block_height - float(auction_info["start_block"]))
        )

        return current_price

    def asset_a(self) -> str:
        return self.asset_a_denom

    def asset_b(self) -> str:
        return self.asset_b_denom

    def remaining_asset_a(self) -> float:
        return float(self.contract.query("get_auction")["available_amount"])


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
                self.deployment_info,
                self.client,
                addr,
                asset_a,
                asset_b,
            )

            if asset_a not in auctions:
                auctions[asset_a] = {}

            auctions[asset_a][asset_b] = provider

        return auctions
