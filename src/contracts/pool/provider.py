"""
Defines an interface for all providers of pricing information to fulfill.
"""

import json
from typing import Any, Optional, cast


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


class PoolProvider:
    """
    A base abstract class representing a pair between two denominations.
    The provider is chain and exchange agnostic, and pricing functionality should be
    exchange and chain-specific via extension of this base class.
    """

    def simulate_swap_asset_a(self, amount: int) -> int:
        """
        Gets the current exchange rate per quantity of asset a in the base denomination.
        """

        raise NotImplementedError

    def simulate_swap_asset_b(self, amount: int) -> int:
        """
        Gets the current exchange rate per quantity of asset b in the base denomination.
        """

        raise NotImplementedError

    def asset_a(self) -> str:
        """
        Gets the contract address or ticker (if a native asset) of the first denomination
        in the pair.
        """

        raise NotImplementedError

    def asset_b(self) -> str:
        """
        Gets the contract address or ticker (if a native asset) of the second denomination
        in the pair.
        """

        raise NotImplementedError

    def dump(self) -> dict[str, Any]:
        """
        Gets a JSON representation of the pool.
        """

        raise NotImplementedError
