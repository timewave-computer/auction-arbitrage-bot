"""
Implements an arbitrage strategy with an arbitrary number
of hops using all available providers.
"""

from decimal import Decimal
import json
from typing import List, Union, Optional, Self, Any
from datetime import datetime, timedelta
from collections import deque
from dataclasses import dataclass
import logging
from src.contracts.pool.provider import PoolProvider
from src.contracts.pool.osmosis import OsmosisPoolProvider
from src.contracts.pool.astroport import (
    NeutronAstroportPoolProvider,
    asset_info_to_token,
)
from src.contracts.auction import AuctionProvider
from src.scheduler import Ctx
from src.util import (
    denom_info_on_chain,
    ContractInfo,
    deployments,
    try_multiple_clients,
    try_multiple_clients_fatal,
    try_multiple_rest_endpoints,
    decimal_to_int,
    IBC_TRANSFER_TIMEOUT_SEC,
    IBC_TRANSFER_POLL_INTERVAL_SEC,
)
from cosmospy_protobuf.ibc.applications.transfer.v1 import tx_pb2  # type: ignore
from cosmospy_protobuf.ibc.core.channel.v1 import query_pb2  # type: ignore
from cosmospy_protobuf.ibc.core.channel.v1 import query_grpc
from cosmpy.crypto.address import Address  # type: ignore
from cosmpy.aerial.tx import Transaction  # type: ignore
from cosmpy.aerial.client.utils import prepare_and_broadcast_basic_transaction  # type: ignore
import grpc
import schedule
from schedule import Scheduler

logger = logging.getLogger(__name__)


@dataclass
class State:
    """
    A strategy state for a naive strategy that provides caching of
    the route graph.
    """

    last_discovered: Optional[datetime]
    routes: Optional[List[List[Union[PoolProvider, AuctionProvider]]]]

    def __load_poolfile(
        self,
        ctx: Ctx,
        endpoints: dict[str, list[str]],
    ) -> None:
        """
        Loads routes from the poolfile provided in the --pool_file flag.
        """

        with open(ctx.cli_args["pool_file"], "r", encoding="utf-8") as f:
            poolfile_cts = json.load(f)

            def poolfile_ent_to_leg(
                ent: dict[str, Any]
            ) -> Union[PoolProvider, AuctionProvider]:
                if "osmosis" in ent:
                    return OsmosisPoolProvider(
                        endpoints,
                        ent["osmosis"]["pool_id"],
                        ent["osmosis"]["asset_a"],
                        ent["osmosis"]["asset_b"],
                    )

                if "neutron_astroport" in ent:
                    return NeutronAstroportPoolProvider(
                        ContractInfo(
                            deployments()["pools"]["astroport"]["neutron"],
                            ctx.clients,
                            ent["neutron_astroport"]["address"],
                            "pair",
                        ),
                        asset_info_to_token(ent["neutron_astroport"]["asset_a"]),
                        asset_info_to_token(ent["neutron_astroport"]["asset_b"]),
                    )

                if "auction" in ent:
                    return AuctionProvider(
                        ContractInfo(
                            deployments()["auctions"]["neutron"],
                            ctx.clients,
                            ent["auction"]["address"],
                            "auction",
                        ),
                        ent["auction"]["asset_a"],
                        ent["auction"]["asset_b"],
                    )

                raise ValueError("Invalid route leg type.")

            if "routes" in poolfile_cts:
                self.routes = [
                    [poolfile_ent_to_leg(ent) for ent in route]
                    for route in poolfile_cts["routes"]
                ]

    def poll(
        self,
        ctx: Ctx,
        pools: dict[str, dict[str, List[PoolProvider]]],
        auctions: dict[str, dict[str, AuctionProvider]],
    ) -> Self:
        """
        Polls the state for a potential update, leaving the state
        alone, or producing a new state.
        """

        # No need to update the state
        if (
            self.last_discovered is not None
            and (datetime.now() - self.last_discovered).total_seconds()
            < ctx.cli_args["discovery_interval"]
        ):
            return self

        endpoints = {
            "http": ["https://lcd.osmosis.zone"],
            "grpc": ["https://osmosis-rpc.publicnode.com:443"],
        }

        if ctx.cli_args["net_config"] is not None:
            with open(ctx.cli_args["net_config"], "r", encoding="utf-8") as f:
                osmo_netconfig = json.load(f)["osmosis"]

                endpoints["http"] = [*endpoints["http"], *osmo_netconfig["http"]]
                endpoints["grpc"] = [*endpoints["grpc"], *osmo_netconfig["grpc"]]

        # Check for a poolfile to specify routes
        if ctx.cli_args["pool_file"] is not None:
            self.__load_poolfile(ctx, endpoints)

        # Store all built routes
        vertices = sum(
            (len(pool_set) for base in pools.values() for pool_set in base.values())
        ) + sum((1 for base in auctions.values() for _ in base.values()))

        # Perform a breadth-first traversal, exploring all possible
        # routes with increasing hops
        logger.info(
            "Building route tree from %s with %d vertices (this may take a while)",
            ctx.cli_args["base_denom"],
            vertices,
        )

        if self.routes is None:
            self.routes: List[List[Union[PoolProvider, AuctionProvider]]] = (
                get_routes_with_depth_limit_bfs(
                    ctx.cli_args["max_hops"],
                    ctx.cli_args["num_routes_considered"],
                    ctx.cli_args["base_denom"],
                    pools,
                    auctions,
                )
            )

        logger.info(
            "Finished building route tree; discovered %d routes",
            len(self.routes),
        )
        self.last_discovered = datetime.now()

        def dump_pool_like(
            pool_like: Union[PoolProvider, AuctionProvider]
        ) -> dict[str, Any]:
            if isinstance(pool_like, AuctionProvider):
                return {
                    "auction": {
                        "asset_a": pool_like.asset_a(),
                        "asset_b": pool_like.asset_b(),
                        "address": pool_like.contract_info.address,
                    }
                }

            if isinstance(pool_like, NeutronAstroportPoolProvider):
                return {"neutron_astroport": pool_like.dump()}

            if isinstance(pool_like, OsmosisPoolProvider):
                return {"osmosis": pool_like.dump()}

            raise ValueError("Invalid route leg type.")

        # The user wants to dump discovered routes, so we can exit now
        if ctx.cli_args["cmd"] == "dump":
            with open(ctx.cli_args["pool_file"], "r+", encoding="utf-8") as f:
                poolfile_cts = json.load(f)
                poolfile_cts["routes"] = [
                    [dump_pool_like(pool) for pool in route] for route in self.routes
                ]

                f.seek(0)
                json.dump(poolfile_cts, f)

                # Exit the event loop and the program
                ctx.cancel()

        return self


def strategy(
    ctx: Ctx,
    pools: dict[str, dict[str, List[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
) -> Ctx:
    """
    Finds new arbitrage opportunities using the context, pools, and auctions.
    """

    if ctx.state is None:
        ctx.state = State(None, None)

    state = ctx.state.poll(ctx, pools, auctions)

    routes: List[List[Union[PoolProvider, AuctionProvider]]] = []
    route_profit: List[int] = []

    # Prices at which each leg in an arb will be executed
    route_price: List[List[int]] = []

    balance_resp = try_multiple_clients(
        ctx.clients,
        lambda client: client.query_bank_balance(
            ctx.wallet.address(), ctx.cli_args["base_denom"]
        ),
    )

    if not balance_resp:
        return ctx

    # Calculate profitability of all routes, and report them
    # to the user
    for route in ctx.state.routes:
        profit, prices = route_base_denom_profit_prices(
            ctx.cli_args["base_denom"],
            balance_resp,
            route,
        )

        if profit >= ctx.cli_args["profit_margin"]:
            routes.append(route)
            route_profit.append(profit)
            route_price.append(prices)

    # Report route stats to user
    logger.info(
        "Found %d profitable routes, with max profit of %d and min profit of %d",
        len(routes),
        max(route_profit),
        min(route_profit),
    )

    for i, (route, profit) in enumerate(zip(routes, route_profit)):
        logger.info(
            (
                "Candidate arbitrage opportunity #%d with "
                "profit of %d and route with %d hop(s): %s"
            ),
            i + 1,
            profit,
            len(route),
            " -> ".join(
                map(
                    lambda route_leg: fmt_route_leg(route_leg)
                    + ": "
                    + route_leg.asset_a()
                    + " - "
                    + route_leg.asset_b(),
                    route,
                )
            ),
        )

    logger.info(
        "Executing %d arbitrage opportunities",
        len(routes),
    )

    swap_balance = balance_resp
    prev_leg: Optional[Union[PoolProvider, AuctionProvider]] = None

    # Submit arb trades for all profitable routes
    for route, leg_prices in zip(routes, route_price):
        for leg, price in zip(route, leg_prices):
            # If the arb leg is on osmosis, move funds to Osmosis
            # and then execute the swap
            if isinstance(leg, OsmosisPoolProvider):
                # The funds are not already on osmosis, so they need to be moved
                if prev_leg and prev_leg.chain_id != "osmosis-1":
                    transfer_osmosis(prev_leg, leg, ctx, swap_balance)

                # Submit the arb trade
                leg.swap_asset_a(
                    ctx.wallet,
                    swap_balance,
                    price,
                    ctx.cli_args["profit_margin"],
                )

            # If the arb leg is on astroport, simply execute the swap
            # on asset A, producing asset B
            if isinstance(leg, NeutronAstroportPoolProvider):
                leg.swap_asset_a(
                    ctx.wallet, swap_balance, price, ctx.cli_args["profit_margin"]
                )

            if isinstance(leg, AuctionProvider):
                leg.swap_asset_a(ctx.wallet, swap_balance)

            prev_leg = leg

    return ctx.with_state(state)


def fmt_route_leg(leg: Union[PoolProvider, AuctionProvider]) -> str:
    """
    Returns the nature of the route leg (i.e., "osmosis," "astroport," or, "valence.")
    """

    if isinstance(leg, OsmosisPoolProvider):
        return "osmosis"

    if isinstance(leg, NeutronAstroportPoolProvider):
        return "astroport"

    if isinstance(leg, AuctionProvider):
        return "valence"

    return "unknown pool"


def transfer_osmosis(
    prev_leg: Union[PoolProvider, AuctionProvider],
    leg: Union[PoolProvider, AuctionProvider],
    ctx: Ctx,
    swap_balance: int,
) -> bool:
    """
    Synchronously executes an IBC transfer from one leg in an arbitrage
    trade to the next, moving `swap_balance` of the asset_b in the source
    leg to asset_a in the destination leg. Returns true if the transfer
    succeeded.
    """

    denom_info_osmo = denom_info_on_chain(
        src_chain=prev_leg.chain_id,
        src_denom=prev_leg.asset_b(),
        dest_chain=leg.chain_id,
    )

    # Not enough info to complete the transfer
    if not denom_info_osmo:
        return False

    # Create a messate transfering the funds
    msg = tx_pb2.MsgTransfer(
        source_port=denom_info_osmo.port,
        source_channel=denom_info_osmo.channel,
        sender=ctx.wallet.address(),
        receiver=Address(ctx.wallet.public_key(), prefix=leg.chain_id.split("-")[0]),
        token=f"{swap_balance}{prev_leg.asset_b()}",
    )

    tx = Transaction()
    tx.add_msg(msg)

    submitted = try_multiple_clients_fatal(
        ctx.clients,
        lambda client: prepare_and_broadcast_basic_transaction(client, tx, ctx.wallet),
    )

    logger.info(
        "Submitted IBC transfer from src %s to %s: %s",
        prev_leg.chain_id,
        leg.chain_id,
        submitted.tx_hash,
    )

    # The IBC transfer failed, so we cannot execute the arb
    if not submitted.response.ensure_successful():
        return False

    # Continuously check for a package acknowledgement
    # or cancel the arb if the timeout passes
    # Future note: This could be async so other arbs can make
    # progress while this is happening
    def transfer_or_continue() -> None:
        if not isinstance(leg, OsmosisPoolProvider):
            return

        # Check for a package acknowledgement by querying osmosis
        ack_resp = try_multiple_rest_endpoints(
            leg.endpoints,
            (
                f"/ibc/core/channel/v1/channels/{denom_info_osmo.channel}/"
                f"ports/{denom_info_osmo.port}/packet_acknowledgement/{submitted.response.events['send_packet']['packet_sequence']}"
            ),
        )

        # Try again
        if not ack_resp:
            return

        # Stop trying, since the transfer succeede
        schedule.clear()

    schedule.every(IBC_TRANSFER_POLL_INTERVAL_SEC).seconds.until(
        timedelta(seconds=IBC_TRANSFER_TIMEOUT_SEC)
    ).do(transfer_or_continue)
    schedule.run_all()

    return True


def route_base_denom_profit_prices(
    base_denom: str,
    starting_amount: int,
    route: List[Union[PoolProvider, AuctionProvider]],
) -> tuple[int, list[int]]:
    """
    Calculates the profit that can be obtained by following the route.
    """

    prev_asset = base_denom
    quantity_received = starting_amount
    prices: list[int] = []

    for leg in route:
        if quantity_received == 0:
            break

        if isinstance(leg, PoolProvider):
            if prev_asset == leg.asset_a():
                new_quantity_received = int(
                    leg.simulate_swap_asset_a(quantity_received)
                )
                prices.append(
                    decimal_to_int(
                        Decimal(new_quantity_received) / Decimal(quantity_received)
                    )
                )
                quantity_received = new_quantity_received
            else:
                new_quantity_received = int(
                    leg.simulate_swap_asset_b(quantity_received)
                )
                prices.append(
                    decimal_to_int(
                        Decimal(new_quantity_received) / Decimal(quantity_received)
                    )
                )
                quantity_received = new_quantity_received
        else:
            if prev_asset == leg.asset_a():
                quantity_received = leg.exchange_rate() * quantity_received
                prices.append(leg.exchange_rate())

    return (quantity_received - starting_amount, prices)


def get_routes_with_depth_limit_bfs(
    depth: int,
    limit: int,
    src: str,
    pools: dict[str, dict[str, List[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
) -> List[List[Union[PoolProvider, AuctionProvider]]]:
    """
    Finds `limit` routes from `src` back to `src` with a maximum route length
    of `depth`.
    """

    to_explore: deque[
        tuple[
            Union[PoolProvider, AuctionProvider],
            tuple[
                List[Union[PoolProvider, AuctionProvider]],
                set[Union[PoolProvider, AuctionProvider]],
            ],
            str,
        ]
    ] = deque(
        [
            (pool, ([], set()), src)
            for pool_set in pools.get(src, {}).values()
            for pool in pool_set
        ]
    )
    paths: List[List[Union[PoolProvider, AuctionProvider]]] = []
    denom_cache = {}

    # Perform a breadth-first traversal, exploring all possible
    # routes from `src` back to itself
    while len(to_explore) > 0 and len(paths) < limit:
        pool, path, base_denom = to_explore.pop()
        pair_denom = pool.asset_a() if pool.asset_a() != base_denom else pool.asset_b()

        if pair_denom not in denom_cache:
            denom_info = denom_info_on_chain("neutron-1", pair_denom, "osmosis-1")

            if not denom_info:
                continue

            denom_cache[pair_denom] = denom_info.denom

        pair_denom_osmo = denom_cache[pair_denom]

        if len(path[0]) + 1 > depth:
            continue

        if pair_denom == src and len(path[0]) > 0:
            path[0].append(pool)
            path[1].add(pool)

            logger.info(
                "Closed circuit from %s to %s; registering route",
                src,
                pair_denom,
            )
            logger.info(
                "Discovered route with %d hop(s): %s",
                len(path[0]),
                " -> ".join(
                    map(
                        lambda route_leg: route_leg.asset_a()
                        + " - "
                        + route_leg.asset_b(),
                        path[0],
                    )
                ),
            )

            paths.append(path[0])

            continue

        # Check auctions first for exploration
        if pair_denom in auctions:
            for candidate_auction in auctions[pair_denom].values():
                if candidate_auction not in path[1] and pool != candidate_auction:
                    to_explore.append(
                        (
                            candidate_auction,
                            (path[0] + [pool], path[1] | {pool}),
                            pair_denom,
                        )
                    )

        for candidate_pool in (
            pool for pool_set in pools[pair_denom].values() for pool in pool_set
        ):
            if candidate_pool not in path[1] and candidate_pool != pool:
                to_explore.append(
                    (candidate_pool, (path[0] + [pool], path[1] | {pool}), pair_denom)
                )

        # Osmosis may not have a matching pool
        if pair_denom_osmo in pools:
            for candidate_pool in (
                (
                    pool
                    for pool_set in pools[pair_denom_osmo].values()
                    for pool in pool_set
                )
                if pair_denom_osmo is not None
                else []
            ):
                if candidate_pool not in path[1] and pool != candidate_pool:
                    to_explore.append(
                        (
                            candidate_pool,
                            (path[0] + [pool], path[1] | {pool}),
                            pair_denom,
                        )
                    )

    return paths
