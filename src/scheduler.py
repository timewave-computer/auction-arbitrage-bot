from src.contracts.auction import AuctionDirectory, AuctionProvider
from src.contracts.pool.provider import PoolProvider
from src.contracts.pool.astroport import AstroportPoolDirectory
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.util import deployments
from typing import Callable, List, Any


class Ctx:
    """
    Information about the scheduling environment including:
    - User configuration via flags
    - User state
    """

    def __init__(self, poll_interval: int) -> None:
        self.poll_interval = poll_interval

    def with_state(self, state: Any) -> Ctx:
        self.state = state

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
        self.ctx = ctx
        self.strategy = strategy
        self.providers: dict[str, dict[str, List[PoolProvider]]] = {}

        auction_manager = AuctionDirectory(deployments())
        self.auctions = auction_manager.auctions()

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
        Polls the strategy functionw with all registered providers.
        """

        self.ctx = self.strategy(self.ctx, self.providers, self.auctions)
