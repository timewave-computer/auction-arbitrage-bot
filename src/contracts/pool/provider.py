"""
Defines an interface for all providers of pricing information to fulfill.
"""

import json
from typing import Any, Optional, cast
from abc import ABC, abstractmethod
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.aerial.tx_helpers import SubmittedTx


def cached_pools(
    poolfile_path: Optional[str], provider: str
) -> Optional[list[dict[str, Any]]]:
    """
    Reads a pool's registered pools from the poolfile. See README.md for format.
    """

    if poolfile_path is None:
        return None

    # If the user specifies a pool dump to use, use that
    with open(poolfile_path, "r", encoding="utf-8") as f:
        poolfile_cts = json.load(f)

        if "pools" in poolfile_cts:
            return cast(list[dict[str, Any]], poolfile_cts["pools"][provider])

    return None


class PoolProvider(ABC):
    """
    A base abstract class representing a pair between two denominations.
    The provider is chain and exchange agnostic, and pricing functionality should be
    exchange and chain-specific via extension of this base class.
    """

    chain_id: str

    chain_prefix: str

    chain_fee_denom: str

    endpoints: list[str]

    swap_fee: int

    kind: str

    @abstractmethod
    async def simulate_swap_asset_a(self, amount: int) -> int:
        """
        Gets the current exchange rate per quantity of asset a in the base denomination.
        """

        raise NotImplementedError

    @abstractmethod
    async def simulate_swap_asset_b(self, amount: int) -> int:
        """
        Gets the current exchange rate per quantity of asset b in the base denomination.
        """

        raise NotImplementedError

    @abstractmethod
    async def reverse_simulate_swap_asset_a(self, amount: int) -> int:
        """
        Gets the amount of asset b required to return a specified amount of asset a.
        """

        raise NotImplementedError

    @abstractmethod
    async def reverse_simulate_swap_asset_b(self, amount: int) -> int:
        """
        Gets the amount of asset a required to return a specified amount of asset b.
        """

        raise NotImplementedError

    @abstractmethod
    def swap_asset_a(
        self, wallet: LocalWallet, amount: int, min_amount: int
    ) -> SubmittedTx:
        """
        Submits a transaction executing a swap with some quantity of asset a
        in exchange for asset b.

        Throws an exception if the swap fails.
        """

        raise NotImplementedError

    @abstractmethod
    def swap_asset_b(
        self, wallet: LocalWallet, amount: int, min_amount: int
    ) -> SubmittedTx:
        """
        Submits a transaction executing a swap with some quantity of asset b
        in exchange for asset a.

        Throws an exception if the swap fails.
        """

        raise NotImplementedError

    @abstractmethod
    def asset_a(self) -> str:
        """
        Gets the contract address or ticker (if a native asset) of the first denomination
        in the pair.
        """

        raise NotImplementedError

    @abstractmethod
    def asset_b(self) -> str:
        """
        Gets the contract address or ticker (if a native asset) of the second denomination
        in the pair.
        """

        raise NotImplementedError

    @abstractmethod
    async def balance_asset_a(self) -> int:
        """
        Gets the quantity of asset A left in the pool.
        """

        raise NotImplementedError

    @abstractmethod
    async def balance_asset_b(self) -> int:
        """
        Gets the quantity of asset B left in the pool.
        """

        raise NotImplementedError

    @abstractmethod
    def dump(self) -> dict[str, Any]:
        """
        Gets a JSON representation of the pool.
        """

        raise NotImplementedError
