"""
Implements a strategy runner with an arbitrary provider set in an event-loop style.
"""

import logging
from datetime import datetime
import json
from typing import Callable, List, Self, Optional, Awaitable, Any, TypeVar, Generic
from dataclasses import dataclass
from cosmpy.aerial.client import LedgerClient
from cosmpy.crypto.address import Address
from cosmpy.aerial.wallet import LocalWallet
from src.contracts.auction import AuctionDirectory, AuctionProvider
from src.contracts.route import Route, load_route, LegRepr, Status, Leg
from src.contracts.pool.provider import PoolProvider
from src.util import (
    try_multiple_clients,
    DenomRouteLeg,
    DenomRouteQuery,
    ChainInfo,
    DenomChainInfo,
)
import aiohttp
import grpc

logger = logging.getLogger(__name__)


MAX_ROUTE_HISTORY_LEN = 200000


# Length to truncate denoms in balance logs to
DENOM_BALANCE_PREFIX_MAX_DENOM_LEN = 12


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
    deployments: dict[str, Any]
    denom_map: dict[str, list[DenomChainInfo]]
    denom_routes: dict[str, dict[str, list[DenomRouteLeg]]]
    chain_info: dict[str, ChainInfo]

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
            self.order_history = [
                load_route(json_route) for json_route in json.load(f)
            ][:-MAX_ROUTE_HISTORY_LEN]

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
            route,
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

        def asset_balance_prefix(leg: Leg, asset: str) -> Optional[str]:
            balance_resp_asset = try_multiple_clients(
                self.clients[leg.backend.chain_id],
                lambda client: client.query_bank_balance(
                    Address(
                        self.wallet.public_key(),
                        prefix=leg.backend.chain_prefix,
                    ),
                    asset,
                ),
            )

            if balance_resp_asset is None or not isinstance(balance_resp_asset, int):
                return None

            return f"balance[{leg.backend.chain_id}]({asset[:DENOM_BALANCE_PREFIX_MAX_DENOM_LEN]}): {balance_resp_asset}"

        def leg_balance_prefixes(leg: Leg) -> list[str]:
            assets = [leg.in_asset(), leg.out_asset()]

            return [
                x for x in (asset_balance_prefix(leg, asset) for asset in assets) if x
            ]

        prefix = " ".join(
            list(
                dict.fromkeys(
                    [
                        prefix
                        for leg_prefixes in [
                            leg_balance_prefixes(leg) for leg in route.legs
                        ]
                        for prefix in leg_prefixes
                    ]
                )
            )
        )

        route.logs.append(f"{log_level.upper()} {prefix} {fmt_string % tuple(args)}")

        if route.uid >= len(self.order_history) or route.uid < 0:
            return

        self.order_history[route.uid] = route

        fmt_string = f"{prefix} %s- {fmt_string}"

        if log_level == "info":
            logger.info(fmt_string, str(route), *args)

            return

        if log_level == "error":
            logger.error(fmt_string, str(route), *args)

            return

        if log_level == "debug":
            logger.debug(fmt_string, str(route), *args)

    async def query_denom_route(
        self, query: DenomRouteQuery
    ) -> Optional[list[DenomRouteLeg]]:
        if (
            self.denom_routes
            and query.src_denom in self.denom_routes
            and query.dest_denom in self.denom_routes[query.src_denom]
        ):
            return self.denom_routes[query.src_denom][query.dest_denom]

        head = {"accept": "application/json", "content-type": "application/json"}

        async with self.http_session.post(
            "https://api.skip.money/v2/fungible/route",
            headers=head,
            json={
                "amount_in": "1",
                "source_asset_denom": query.src_denom,
                "source_asset_chain_id": query.src_chain,
                "dest_asset_denom": query.dest_denom,
                "dest_asset_chain_id": query.dest_chain,
                "allow_multi_tx": True,
                "allow_unsafe": False,
                "bridges": ["IBC"],
            },
        ) as resp:
            if resp.status != 200:
                return None

            ops = (await resp.json())["operations"]

            # The transfer includes a swap or some other operation
            # we can't handle
            if any(("transfer" not in op for op in ops)):
                return None

            transfer_info = ops[0]["transfer"]

            from_chain_info = await self.query_chain_info(
                transfer_info["from_chain_id"]
            )
            to_chain_info = await self.query_chain_info(transfer_info["to_chain_id"])

            if not from_chain_info or not to_chain_info:
                return None

            route = [
                DenomRouteLeg(
                    src_chain=query.src_chain,
                    dest_chain=query.dest_chain,
                    src_denom=query.src_denom,
                    dest_denom=query.dest_denom,
                    from_chain=from_chain_info,
                    to_chain=to_chain_info,
                    port=transfer_info["port"],
                    channel=transfer_info["channel"],
                )
                for op in ops
            ]

            self.denom_routes.get(query.src_denom, {})[query.dest_denom] = route

            return route

    async def query_chain_info(
        self,
        chain_id: str,
    ) -> Optional[ChainInfo]:
        """
        Gets basic information about a cosmos chain.
        """

        if chain_id in self.chain_info:
            return self.chain_info[chain_id]

        head = {"accept": "application/json", "content-type": "application/json"}

        async with self.http_session.get(
            f"https://api.skip.money/v2/info/chains?chain_ids={chain_id}",
            headers=head,
        ) as resp:
            if resp.status != 200:
                return None

            chains = (await resp.json())["chains"]

            if len(chains) == 0:
                return None

            chain = chains[0]

            chain_info = ChainInfo(
                chain_name=chain["chain_name"],
                chain_id=chain["chain_id"],
                pfm_enabled=chain["pfm_enabled"],
                supports_memo=chain["supports_memo"],
                bech32_prefix=chain["bech32_prefix"],
                fee_asset=chain["fee_assets"][0]["denom"],
                chain_type=chain["chain_type"],
                pretty_name=chain["pretty_name"],
            )

            self.chain_info[chain_id] = chain_info

            return chain_info

    async def query_denom_info_on_chain(
        self,
        src_chain: str,
        src_denom: str,
        dest_chain: str,
    ) -> Optional[DenomChainInfo]:
        """
        Gets a neutron denom's denom and channel on/to another chain.
        """

        infos = await self.query_denom_info(src_chain, src_denom)

        return next((info for info in infos if info.dest_chain_id == dest_chain))

    async def query_denom_info(
        self,
        src_chain: str,
        src_denom: str,
    ) -> list[DenomChainInfo]:
        """
        Gets a denom's denom and channel on/to other chains.
        """

        if src_denom in self.denom_map:
            return self.denom_map[src_denom]

        head = {"accept": "application/json", "content-type": "application/json"}

        async with self.http_session.post(
            "https://api.skip.money/v1/fungible/assets_from_source",
            headers=head,
            json={
                "allow_multi_tx": False,
                "include_cw20_assets": True,
                "source_asset_denom": src_denom,
                "source_asset_chain_id": src_chain,
                "client_id": "timewave-arb-bot",
            },
        ) as resp:
            if resp.status != 200:
                return []

            dests = (await resp.json())["dest_assets"]

            def chain_info(chain_id: str, info: dict[str, Any]) -> DenomChainInfo:
                info = info["assets"][0]

                return DenomChainInfo(
                    src_chain_id=src_chain, denom=info["denom"], dest_chain_id=chain_id
                )

            infos = [chain_info(chain_id, info) for chain_id, info in dests.items()]

            self.denom_map[src_denom] = infos

            return infos


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
            self.ctx.deployments,
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
                for endpoint in ctx.endpoints[
                    list(self.ctx.deployments["auctions"].keys())[0]
                ]["grpc"]
            ],
            endpoints=ctx.endpoints[list(self.ctx.deployments["auctions"].keys())[0]],
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
