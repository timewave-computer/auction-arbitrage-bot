from datetime import datetime
import json
from enum import Enum
from dataclasses import dataclass
from typing import Union, Callable, Optional, Any
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
    execution_height: Optional[int]

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
    legs: list[Leg]
    theoretical_profit: int
    expected_profit: int
    realized_profit: Optional[int]
    quantities: list[int]
    status: Status
    time_created: datetime
    logs: list[str]
    logs_enabled: bool

    def __hash__(self) -> int:
        return hash(self.uid)

    def __str__(self) -> str:
        return f"r{self.uid}"

    def db_row(self) -> list[Any]:
        """
        Creates a tuple representing the route's metadata for
        persistence purposes.
        Expects insertioni columns (theoretical_profit,
        expected_profit,
        realized_profit,
        status,
        time_created,
        logs_enabled)
        """

        return [
            str(self.theoretical_profit),
            str(self.expected_profit),
            str(self.realized_profit),
            str(self.status),
            self.time_created,
            self.logs_enabled,
        ]

    def legs_db_rows(self, order_uid: int) -> list[list[Any]]:
        """
        Creates a db row for each leg in the route.
        Expects insertion columns (in_amount, out_amount, in_asset, out_asset, kind, executed)
        """

        legs = []

        for i, (leg, leg_repr, in_amount) in enumerate(
            zip(self.legs, self.route, self.quantities)
        ):
            out_amount = self.quantities[i + 1]

            legs.append(
                [
                    i,
                    order_uid,
                    str(in_amount),
                    str(out_amount),
                    leg.in_asset(),
                    leg.out_asset(),
                    leg_repr.kind,
                    leg_repr.executed,
                    leg_repr.execution_height,
                ]
            )

        return legs

    def logs_db_rows(self, order_uid: int) -> list[tuple[int, int, str]]:
        """
        Creates a db row for each log in the route.
        Expects insertionc columns (contents)
        """

        return [(i, order_uid, log) for (i, log) in enumerate(self.logs)]

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
