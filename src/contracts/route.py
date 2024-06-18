import json
from enum import Enum
from dataclasses import dataclass
from typing import Union, Callable, Optional
from src.contracts.auction import AuctionProvider
from src.contracts.pool.provider import PoolProvider


# Possible states for an arbitrage order
class Status(Enum):
    QUEUED = "QUEUED"
    ABORTED = "ABORTED"
    IN_PROGRESS = "IN_PROGRESS"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    RECOVERED = "RECOVERED"


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


@dataclass
class LegRepr:
    in_asset: str
    out_asset: str
    kind: str
    executed: bool

    def __str__(self) -> str:
        return f"{self.kind}: {self.in_asset} -> {self.out_asset}"


@dataclass
class Route:
    """
    Represents an identifiable sequence of arbitrage legs
    that is going to be executed.
    """

    uid: int
    route: list[LegRepr]
    theoretical_profit: int
    expected_profit: int
    realized_profit: Optional[int]
    quantities: list[int]
    status: Status
    time_created: str
    logs: list[str]

    def __hash__(self) -> int:
        return hash(self.uid)

    def __str__(self) -> str:
        return f"r{self.uid}"

    def fmt_pretty(self) -> str:
        route_fmt = " -> ".join(map(lambda route_leg: str(route_leg), self.route))

        return f"{str(self)} ({self.time_created}) expected ROI: {self.expected_profit}, realized P/L: {self.realized_profit}, status: {self.status}, path: {route_fmt}, execution plan: {self.quantities}"

    def dumps(self) -> str:
        return json.dumps(
            {
                "uid": self.uid,
                "route": [
                    json.dumps(
                        {
                            "in_asset": leg.in_asset,
                            "out_asset": leg.out_asset,
                            "kind": leg.kind,
                            "executed": leg.executed,
                        }
                    )
                    for leg in self.route
                ],
                "theoretical_profit": self.theoretical_profit,
                "expected_profit": self.expected_profit,
                "realized_profit": self.realized_profit,
                "quantities": self.quantities,
                "status": str(self.status),
                "time_created": self.time_created,
                "logs": self.logs,
            }
        )


def load_route(s: str) -> Route:
    loaded = json.loads(s)

    return Route(
        loaded["uid"],
        [load_leg_repr(json_leg) for json_leg in loaded["route"]],
        loaded["theoretical_profit"],
        loaded["expected_profit"],
        loaded["realized_profit"],
        loaded["quantities"],
        Status[loaded["status"].split(".")[1]],
        loaded["time_created"],
        loaded["logs"],
    )


def load_leg_repr(s: str) -> LegRepr:
    loaded = json.loads(s)

    return LegRepr(
        loaded["in_asset"], loaded["out_asset"], loaded["kind"], loaded["executed"]
    )
