"""
Defines an interface for all providers of pricing information to fulfill.
"""


class PoolProvider:
    """
    A base abstract class representing a pair between two denominations.
    The provider is chain and exchange agnostic, and pricing functionality should be
    exchange and chain-specific via extension of this base class.
    """

    def simulate_swap_asset_a(self, amount: int) -> float:
        """
        Gets the current exchange rate per quantity of asset a in the base denomination.
        """

        raise NotImplementedError

    def simulate_swap_asset_b(self, amount: int) -> float:
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
