from src.contracts.auction import AuctionDirectory, AuctionProvider
from src.contracts.pool.provider import PoolProvider
from src.contracts.pool.astroport import AstroportPoolDirectory
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.util import deployments
from cosmpy.aerial.client import LedgerClient  # type: ignore
from typing import Callable, List, Any, Self, Optional


class Ctx:
    """
    Information about the scheduling environment including:
    - User configuration via flags
    - User state
    """

    client: LedgerClient
    poll_interval: int
    discovery_interval: int
    max_hops: int
    num_routes_considered: int
    base_denom: str
    profit_margin: int
    wallet_address: str
    state: Optional[Any]

    def __init__(
        self,
        client: LedgerClient,
        poll_interval: int,
        discovery_interval: int,
        max_hops: int,
        num_routes_considered: int,
        base_denom: str,
        profit_margin: int,
        wallet_address: str,
    ) -> None:
        self.client = client
        self.poll_interval = poll_interval
        self.discovery_interval = discovery_interval
        self.max_hops = max_hops
        self.num_routes_considered = num_routes_considered
        self.base_denom = base_denom
        self.profit_margin = profit_margin
        self.wallet_address = wallet_address
        self.state = None

    def with_state(self, state: Any) -> Self:
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
