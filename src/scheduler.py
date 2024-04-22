"""
Implements a strategy runner with an arbitrary provider set in an event-loop style.
"""

from typing import Callable, List, Any, Self, Optional
from dataclasses import dataclass
from cosmpy.aerial.client import LedgerClient  # type: ignore
from cosmpy.aerial.wallet import LocalWallet  # type: ignore
from src.contracts.auction import AuctionDirectory, AuctionProvider
from src.contracts.pool.provider import PoolProvider
from src.util import deployments


@dataclass
class Ctx:
    """
    Information about the scheduling environment including:
    - User configuration via flags
    - User state
    """

    clients: list[LedgerClient]
    wallet: LocalWallet
    cli_args: dict[str, Any]
    state: Optional[Any]
    terminated: bool

    def with_state(self, state: Any) -> Self:
        """
        Constructs a new context with the given state,
        modifying the current context.
        """

        self.state = state

        return self

    def cancel(self) -> Self:
        """
        Marks the event loop for termination.
        """

        self.terminated = True

        return self


class Scheduler:
    """
    A registry of pricing providers for different assets,
    which can be polled alongside a strategy function which
    may interact with registered providers.
    """

    ctx: Ctx
    strategy: Callable[
        [
            Ctx,
            dict[str, dict[str, List[PoolProvider]]],
            dict[str, dict[str, AuctionProvider]],
        ],
        Ctx,
    ]
    providers: dict[str, dict[str, List[PoolProvider]]]
    auction_manager: AuctionDirectory
    auctions: dict[str, dict[str, AuctionProvider]]

    def __init__(
        self,
        ctx: Ctx,
        strategy: Callable[
            [
                Ctx,
                dict[str, dict[str, List[PoolProvider]]],
                dict[str, dict[str, AuctionProvider]],
            ],
            Ctx,
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
            deployments(), poolfile_path=ctx.cli_args["pool_file"]
        )
        self.auctions = self.auction_manager.auctions()

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

    def poll(self) -> None:
        """
        Polls the strategy function with all registered providers.
        """

        self.ctx = self.strategy(self.ctx, self.providers, self.auctions)
