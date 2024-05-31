from dataclasses import dataclass
from typing import Union, Callable
from src.contracts.auction import AuctionProvider
from src.contracts.pool.provider import PoolProvider


@dataclass
class Route:
    """
    Represents an identifiable sequence of arbitrage legs.
    """

    uuid: str
    route: list[Leg]

    def __hash__(self) -> int:
        return hash(self.uuid)

    def __str__(self) -> str:
        return f"r{self.uuid}"

    def fmt_pretty(self) -> str:
        route_fmt = " -> ".join(map(lambda route_leg: str(route_leg), self.route))

        return f"{str(self)} {route_fmt}"


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

    def __str__(self) -> str:
        return f"{self.backend.kind}: {self.in_asset()} -> {self.out_asset()}"
