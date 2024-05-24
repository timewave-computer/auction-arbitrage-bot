from dataclasses import dataclass
from typing import Union, Callable
from src.contracts.auction import AuctionProvider
from src.contracts.pool.provider import PoolProvider


@dataclass
class Leg:
    """
    Represents an auction or a poolprovider with a particularly ordered asset pair.
    """

    in_asset: Callable[[], str]
    out_asset: Callable[[], str]
    backend: Union[AuctionProvider, PoolProvider]

    def __hash__(self) -> int:
        return hash((self.backend, hash(self.in_asset()), hash(self.out_asset())))
