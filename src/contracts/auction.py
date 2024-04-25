"""
Implements a wrapper around a valence auction, providing pricing information
for the auction.
"""

import json
from decimal import Decimal
from typing import Any, List, Optional
from cosmpy.aerial.contract import LedgerContract  # type: ignore
from cosmpy.aerial.wallet import LocalWallet  # type: ignore
from cosmpy.aerial.client import LedgerClient, NetworkConfig  # type: ignore
from cosmpy.aerial.tx_helpers import SubmittedTx  # type: ignore
from src.util import (
    NEUTRON_NETWORK_CONFIG,
    WithContract,
    ContractInfo,
    decimal_to_int,
    try_query_multiple,
    try_multiple_clients,
    try_exec_multiple_fatal,
)


class AuctionProvider(WithContract):
    """
    Provides pricing and asset information for an arbitrary auction on valenece.
    """

    def __init__(
        self,
        contract_info: ContractInfo,
        asset_a: str,
        asset_b: str,
    ):
        WithContract.__init__(self, contract_info)
        self.asset_a_denom = asset_a
        self.asset_b_denom = asset_b

    def exchange_rate(self) -> int:
        """
        Gets the number of asset_b required to purchase a single asset_a.
        """

        auction_info = try_query_multiple(self.contracts, "get_auction")

        if not auction_info:
            return 0

        # No swap is possible since the auction is closed
        if auction_info["status"] != "started":
            return 0

        # Calculate prices manually by following the
        # pricing curve to the given block
        current_block_height = try_multiple_clients(
            self.contract_info.clients, lambda client: client.query_height()
        )

        if not current_block_height:
            return 0

        start_price = Decimal(auction_info["start_price"])
        end_price = Decimal(auction_info["end_price"])
        start_block = Decimal(auction_info["start_block"])
        end_block = Decimal(auction_info["end_block"])

        # The change in price per block
        price_delta_per_block: Decimal = (start_price - end_price) / (
            end_block - start_block
        )

        # The current price
        current_price: Decimal = start_price - (
            price_delta_per_block * (current_block_height - start_block)
        )

        return decimal_to_int(current_price)

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

    def swap_asset_a(
        self, wallet: LocalWallet, amount: int, price: int, max_spread: int
    ) -> SubmittedTx:
        return try_exec_multiple_fatal(
            self.contracts, wallet, {"bid": {}}, funds=f"{amount}{self.asset_a_denom}"
        )

    def remaining_asset_a(self) -> int:
        """
        Gets the amount of the asking asset left in the auction.
        """

        res = try_query_multiple(self.contracts, "get_auction")

        if not res:
            return 0

        return int(res["available_amount"])

    def __hash__(self) -> int:
        return hash(self.contract_info.address)


class AuctionDirectory:
    """
    A wrapper around an auction manager providing:
    - Accessors for all auctions on valence
    - AuctionProviders for each auction
    """

    cached_auctions: Optional[list[dict[str, Any]]]

    def __init__(
        self,
        deployments: dict[str, Any],
        poolfile_path: Optional[str] = None,
        network_configs: Optional[list[NetworkConfig]] = None,
    ) -> None:
        self.clients = [
            LedgerClient(NEUTRON_NETWORK_CONFIG),
            *(network_configs if network_configs else []),
        ]
        self.deployment_info = deployments["auctions"]["neutron"]
        self.cached_auctions = None

        # The user wants to load auctions from the poolfile
        if poolfile_path is not None:
            with open(poolfile_path, "r", encoding="utf-8") as f:
                poolfile_cts = json.load(f)

                if "auctions" in poolfile_cts:
                    self.cached_auctions = poolfile_cts["auctions"]

                    return

        deployment_info = self.deployment_info["auctions_manager"]
        self.directory_contract = [
            LedgerContract(
                deployment_info["src"], client, address=deployment_info["address"]
            )
            for client in self.clients
        ]

    def __auctions_cached(self) -> dict[str, dict[str, AuctionProvider]]:
        """
        Reads the auctions in the AuctionPoolProvider from the contents of the pool file.
        """

        if self.cached_auctions is None:
            return {}

        auctions: dict[str, dict[str, AuctionProvider]] = {}

        for poolfile_entry in self.cached_auctions:
            asset_a, asset_b = (
                poolfile_entry["asset_a"],
                poolfile_entry["asset_b"],
            )
            provider = AuctionProvider(
                ContractInfo(
                    self.deployment_info,
                    self.clients,
                    poolfile_entry["address"],
                    "auction",
                ),
                asset_a,
                asset_b,
            )

            # Register the auction
            if asset_a not in auctions:
                auctions[asset_a] = {}

            if asset_b not in auctions:
                auctions[asset_b] = {}

            auctions[asset_a][asset_b] = provider
            auctions[asset_b][asset_a] = provider

        return auctions

    def auctions(self) -> dict[str, dict[str, AuctionProvider]]:
        """
        Gets an AuctionProvider for every pair on valence.
        """

        if self.cached_auctions is not None:
            return self.__auctions_cached()

        auction_infos = try_query_multiple(
            self.directory_contract, {"get_pairs": {"start_after": None, "limit": None}}
        )

        if not auction_infos:
            return {}

        auctions: dict[str, dict[str, AuctionProvider]] = {}

        for auction in auction_infos:
            pair, addr = auction
            asset_a, asset_b = pair

            provider = AuctionProvider(
                ContractInfo(self.deployment_info, self.clients, addr, "auction"),
                asset_a,
                asset_b,
            )

            if asset_a not in auctions:
                auctions[asset_a] = {}

            auctions[asset_a][asset_b] = provider

        return auctions

    def contract(self) -> Optional[list[LedgerContract]]:
        """
        Gets the contract backing the auction directory.
        """

        if self.cached_auctions is not None:
            return None

        return self.directory_contract

    @staticmethod
    def dump_auctions(
        auctions: dict[str, dict[str, AuctionProvider]]
    ) -> List[dict[str, Any]]:
        """
        Gets a JSON representation of the specified auctions.
        """

        return list(
            {
                auction.contract_info.address: {
                    "asset_a": auction.asset_a(),
                    "asset_b": auction.asset_b(),
                    "address": auction.contract_info.address,
                }
                for base in auctions.values()
                for auction in base.values()
            }.values()
        )
