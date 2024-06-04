"""
Implements a strategy runner with an arbitrary provider set in an event-loop style.
"""

import logging
from datetime import datetime
import json
from typing import Callable, List, Self, Optional, Awaitable, Any, TypeVar, Generic
from dataclasses import dataclass
from cosmpy.aerial.client import LedgerClient
from cosmpy.aerial.wallet import LocalWallet
from src.contracts.auction import AuctionDirectory, AuctionProvider
from src.contracts.route import Route, load_route, LegRepr, Status, Leg
from src.contracts.pool.provider import PoolProvider
from src.util import deployments
import aiohttp
import grpc

logger = logging.getLogger(__name__)

TState = TypeVar("TState")


@dataclass
class Ctx(Generic[TState]):
    """
    Information about the scheduling environment including:
    - User configuration via flags
    - User state
    """

    clients: dict[str, list[LedgerClient]]
    endpoints: dict[str, dict[str, list[str]]]
    wallet: LocalWallet
    cli_args: dict[str, Any]
    state: Optional[TState]
    terminated: bool
    http_session: aiohttp.ClientSession
    order_history: list[Route]

    def with_state(self, state: Any) -> Self:
        """
        Constructs a new context with the given state,
        modifying the current context.
        """

        self.state = state

        return self

    def commit_history(self) -> Self:
        """
        Commits the order history to disk.
        """

        with open(self.cli_args["history_file"], "w", encoding="utf-8") as f:
            f.seek(0)
            json.dump([order.dumps() for order in self.order_history], f)

        return self

    def recover_history(self) -> Self:
        """
        Retrieves the order history from disk
        """

        with open(self.cli_args["history_file"], "r", encoding="utf-8") as f:
            f.seek(0)
            self.order_history = [load_route(json_route) for json_route in json.load(f)]

        return self

    def cancel(self) -> Self:
        """
        Marks the event loop for termination.
        """

        self.terminated = True

        return self

    def queue_route(
        self,
        route: list[Leg],
        theoretical_profit: int,
        expected_profit: int,
        quantities: list[int],
    ) -> Route:
        """
        Creates a new identified route, inserting it into the order history,
        and returning it for later updating.
        """

        r = Route(
            len(self.order_history),
            [
                LegRepr(leg.in_asset(), leg.out_asset(), leg.backend.kind, False)
                for leg in route
            ],
            theoretical_profit,
            expected_profit,
            None,
            quantities,
            Status.QUEUED,
            datetime.now().strftime("%Y-%m-%d @ %H:%M:%S"),
            [],
        )
        self.order_history.append(r)

        return r

    def update_route(self, route: Route) -> None:
        """
        Updates the route in the scheduler.
        """

        if route.uid >= len(self.order_history) or route.uid < 0:
            return

        self.order_history[route.uid] = route

    def log_route(
        self, route: Route, log_level: str, fmt_string: str, args: list[Any]
    ) -> None:
        """
        Writes a log to the standard logger and to the log file of a route.
        """

        route.logs.append(f"{log_level.upper()} {fmt_string % tuple(args)}")

        if route.uid >= len(self.order_history) or route.uid < 0:
            return

        self.order_history[route.uid] = route

        fmt_string = f"%s- {fmt_string}"

        if log_level == "info":
            logger.info(fmt_string, str(route), *args)

            return

        if log_level == "error":
            logger.error(fmt_string, str(route), *args)

            return

        if log_level == "debug":
            logger.debug(fmt_string, str(route), *args)


class Scheduler(Generic[TState]):
    """
    A registry of pricing providers for different assets,
    which can be polled alongside a strategy function which
    may interact with registered providers.
    """

    ctx: Ctx[TState]
    strategy: Callable[
        [
            Ctx[TState],
            dict[str, dict[str, List[PoolProvider]]],
            dict[str, dict[str, AuctionProvider]],
        ],
        Awaitable[Ctx[TState]],
    ]
    providers: dict[str, dict[str, List[PoolProvider]]]
    auction_manager: AuctionDirectory
    auctions: dict[str, dict[str, AuctionProvider]]

    def __init__(
        self,
        ctx: Ctx[TState],
        strategy: Callable[
            [
                Ctx[TState],
                dict[str, dict[str, List[PoolProvider]]],
                dict[str, dict[str, AuctionProvider]],
            ],
            Awaitable[Ctx[TState]],
        ],
    ) -> None:
        """
        Initializes the scheduler with some initial context,
        a strategy function to poll,
        and no providers except available auctions.
        """
        self.ctx = ctx
        self.strategy = strategy
        self.providers: dict[str, dict[str, List[PoolProvider]]] = {}

        self.auction_manager = AuctionDirectory(
            deployments(),
            ctx.http_session,
            [
                (
                    grpc.aio.secure_channel(
                        endpoint.split("grpc+https://")[1],
                        grpc.ssl_channel_credentials(),
                    )
                    if "https" in endpoint
                    else grpc.aio.insecure_channel(
                        endpoint.split("grpc+http://")[1],
                    )
                )
                for endpoint in ctx.endpoints["neutron"]["grpc"]
            ],
            endpoints=ctx.endpoints["neutron"],
            poolfile_path=ctx.cli_args["pool_file"],
        )
        self.auctions = {}

    async def register_auctions(self) -> None:
        """
        Registers all auctions into the pool provider.
        """

        self.auctions = await self.auction_manager.auctions()

    def register_provider(self, provider: PoolProvider) -> None:
        """
        Registers a pool provider, enqueing it to future strategy function polls.
        """

        if provider.asset_a() not in self.providers:
            self.providers[provider.asset_a()] = {}

        if provider.asset_b() not in self.providers:
            self.providers[provider.asset_b()] = {}

        if provider.asset_b() not in self.providers[provider.asset_a()]:
            self.providers[provider.asset_a()][provider.asset_b()] = []

        if provider.asset_a() not in self.providers[provider.asset_b()]:
            self.providers[provider.asset_b()][provider.asset_a()] = []

        self.providers[provider.asset_a()][provider.asset_b()].append(provider)
        self.providers[provider.asset_b()][provider.asset_a()].append(provider)

    async def poll(self) -> None:
        """
        Polls the strategy function with all registered providers.
        """

        self.ctx = await self.strategy(self.ctx, self.providers, self.auctions)
