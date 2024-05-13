"""
Implements an arbitrage strategy with an arbitrary number
of hops using all available providers.
"""

import threading
from queue import Queue
from decimal import Decimal
import json
from typing import List, Union, Optional, Self, Any
from datetime import datetime, timedelta
import time
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
    DenomChainInfo,
    denom_info_on_chain,
    denom_info,
    ContractInfo,
    deployments,
    try_multiple_clients,
    try_multiple_clients_fatal,
    try_multiple_rest_endpoints,
    decimal_to_int,
    int_to_decimal,
    IBC_TRANSFER_TIMEOUT_SEC,
    IBC_TRANSFER_POLL_INTERVAL_SEC,
)
from ibc.applications.transfer.v1 import tx_pb2
from ibc.core.channel.v1 import query_pb2
from ibc.core.channel.v1 import query_pb2_grpc
from cosmos.base.v1beta1 import coin_pb2
from cosmpy.crypto.address import Address  # type: ignore
from cosmpy.aerial.tx import Transaction, SigningCfg  # type: ignore
from cosmpy.aerial.client.utils import prepare_and_broadcast_basic_transaction  # type: ignore
from cosmpy.aerial.tx_helpers import SubmittedTx  # type: ignore
import grpc
import schedule
from readerwriterlock import rwlock
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
                        ent["osmosis"]["address"],
                        ent["osmosis"]["pool_id"],
                        (ent["osmosis"]["asset_a"], ent["osmosis"]["asset_b"]),
                    )

                if "neutron_astroport" in ent:
                    return NeutronAstroportPoolProvider(
                        ContractInfo(
                            deployments()["pools"]["astroport"]["neutron"],
                            ctx.clients["neutron"],
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
                            ctx.clients["neutron"],
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
            "grpc": ["grpc+https://osmosis-grpc.publicnode.com:443"],
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
                get_routes_with_depth_limit_dfs(
                    ctx.cli_args["hops"],
                    ctx.cli_args["num_routes_considered"],
                    ctx.cli_args["base_denom"],
                    set(ctx.cli_args["require_leg_types"]),
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

    workers = []
    to_exec: Queue[List[Union[PoolProvider, AuctionProvider]]] = Queue(maxsize=0)
    is_exec = rwlock.RWLockFair()

    # Report route stats to user
    logger.info(
        "Finding profitable routes",
    )

    def profit_arb(i: int, route: List[Union[PoolProvider, AuctionProvider]]) -> None:
        with is_exec.gen_rlock():
            balance_resp = try_multiple_clients(
                ctx.clients["neutron"],
                lambda client: client.query_bank_balance(
                    Address(ctx.wallet.public_key(), prefix="neutron"),
                    ctx.cli_args["base_denom"],
                ),
            )

            if not balance_resp:
                return

            profit, _ = route_base_denom_profit_quantities(
                ctx.cli_args["base_denom"],
                balance_resp,
                route,
            )

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

            # The trade is profitable. Execute the arb
            if profit >= ctx.cli_args["profit_margin"]:
                logger.info("Queueing candidate arbitrage opportunity #%d", i + 1)
                to_exec.put(route)

    # Calculate profitability of all routes, and execute
    # profitable ones
    for i, route in enumerate(ctx.state.routes):
        workers.append(threading.Thread(target=profit_arb, args=[i, route]))
        workers[-1].start()

    while True:
        route = to_exec.get()

        with is_exec.gen_wlock():
            exec_arb(route, ctx)

    for worker in workers:
        worker.join()

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


def exec_arb(
    route: List[Union[AuctionProvider, PoolProvider]],
    ctx: Ctx,
) -> None:
    """
    Executes a list of arbitrage trades composed of multiple hops.

    Takes a list of arbitrage trades, and a list of prices for each hop
    in each trade.
    """

    swap_balance: Optional[int] = try_multiple_clients(
        ctx.clients["neutron"],
        lambda client: client.query_bank_balance(
            Address(ctx.wallet.public_key(), prefix="neutron"),
            ctx.cli_args["base_denom"],
        ),
    )

    if not swap_balance:
        return

    _, quantities = route_base_denom_profit_quantities(
        ctx.cli_args["base_denom"],
        swap_balance,
        route,
    )

    prev_leg: Optional[Union[PoolProvider, AuctionProvider]] = None
    prev_asset = ctx.cli_args["base_denom"]

    # Submit arb trades for all profitable routes
    logger.info(
        ("Queueing candidate arbitrage opportunity with " "route with %d hop(s): %s"),
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

    for leg in route:
        tx: Optional[SubmittedTx] = None

        prev_asset_info: Optional[DenomChainInfo] = None

        if prev_leg:
            prev_asset_info = denom_info_on_chain(
                prev_leg.chain_id, prev_asset, leg.chain_id
            )

        # Match the previous leg's swapped-to asset with
        # the current leg's swap from asset
        assets = (
            [leg.asset_a, leg.asset_b]
            if prev_asset == leg.asset_a()
            or (prev_leg and prev_asset_info and prev_asset_info.denom == leg.asset_a())
            else [leg.asset_b, leg.asset_a]
        )

        to_swap = (
            swap_balance
            if not prev_leg
            else try_multiple_clients_fatal(
                ctx.clients[prev_leg.chain_id.split("-")[0]],
                lambda client: client.query_bank_balance(
                    Address(ctx.wallet.public_key(), prefix=prev_leg.chain_prefix),
                    prev_asset,
                ),
            )
        )

        logger.info(
            "Executing arb leg on %s with %d %s -> %s",
            fmt_route_leg(leg),
            to_swap,
            assets[0](),
            assets[1](),
        )

        # The funds are not already on the current chain, so they need to be moved
        if prev_leg and prev_leg.chain_id != leg.chain_id:
            transfer(prev_asset, prev_leg, leg, ctx, to_swap)

            to_swap = min(
                try_multiple_clients_fatal(
                    ctx.clients[leg.chain_id.split("-")[0]],
                    lambda client: client.query_bank_balance(
                        Address(ctx.wallet.public_key(), prefix=leg.chain_prefix),
                        assets[0](),
                    ),
                ),
                to_swap,
            )

        # If the arb leg is on astroport, simply execute the swap
        # on asset A, producing asset B
        if isinstance(leg, PoolProvider):
            to_receive = (
                leg.simulate_swap_asset_a(to_swap)
                if assets[0] == leg.asset_a
                else leg.simulate_swap_asset_b(to_swap)
            )

            if isinstance(leg, NeutronAstroportPoolProvider):
                logger.info("Submitting arb to contract: %s", leg.contract_info.address)

            if isinstance(leg, OsmosisPoolProvider):
                logger.info("Submitting arb to pool: %d", leg.pool_id)

            # Submit the arb trade
            if assets[0] == leg.asset_a:
                tx = leg.swap_asset_a(
                    ctx.wallet,
                    to_swap,
                    to_receive,
                ).wait_to_complete()
            else:
                tx = leg.swap_asset_b(
                    ctx.wallet,
                    to_swap,
                    to_receive,
                ).wait_to_complete()

        if isinstance(leg, AuctionProvider) and assets[0] == leg.asset_a:
            tx = leg.swap_asset_a(ctx.wallet, to_swap).wait_to_complete()

        if tx:
            # Notify the user of the arb trade
            logger.info(
                "Executed leg %s -> %s: %s",
                assets[0](),
                assets[1](),
                tx.tx_hash,
            )

        prev_leg = leg
        prev_asset = assets[1]()


def transfer(
    denom: str,
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

    denom_info = denom_info_on_chain(
        src_chain=prev_leg.chain_id,
        src_denom=denom,
        dest_chain=leg.chain_id,
    )

    if not denom_info:
        return False

    channel_info = try_multiple_rest_endpoints(
        ctx.endpoints[leg.chain_id.split("-")[0]]["http"],
        f"/ibc/core/channel/v1/channels/{denom_info.channel}/ports/{denom_info.port}",
    )

    if not channel_info:
        return False

    # Not enough info to complete the transfer
    if not denom_info or not denom_info.port or not denom_info.channel:
        return False

    acc = try_multiple_clients_fatal(
        ctx.clients[prev_leg.chain_id.split("-")[0]],
        lambda client: client.query_account(
            str(Address(ctx.wallet.public_key(), prefix=prev_leg.chain_prefix))
        ),
    )

    # Create a messate transfering the funds
    msg = tx_pb2.MsgTransfer(  # pylint: disable=no-member
        source_port=channel_info["channel"]["counterparty"]["port_id"],
        source_channel=channel_info["channel"]["counterparty"]["channel_id"],
        sender=str(Address(ctx.wallet.public_key(), prefix=prev_leg.chain_prefix)),
        receiver=str(Address(ctx.wallet.public_key(), prefix=leg.chain_prefix)),
        timeout_timestamp=time.time_ns() + 600 * 10**9,
    )
    msg.token.CopyFrom(
        coin_pb2.Coin(  # pylint: disable=maybe-no-member
            denom=denom, amount=str(swap_balance - 50000)
        )
    )

    tx = Transaction()
    tx.add_message(msg)
    tx.seal(
        SigningCfg.direct(ctx.wallet.public_key(), acc.sequence),
        f"50000{prev_leg.chain_fee_denom}",
        500000,
    )
    tx.sign(ctx.wallet.signer(), prev_leg.chain_id, acc.number)
    tx.complete()

    submitted = try_multiple_clients_fatal(
        ctx.clients[prev_leg.chain_id.split("-")[0]],
        lambda client: client.broadcast_tx(tx),
    )

    logger.info(
        "Submitted IBC transfer from src %s to %s: %s",
        prev_leg.chain_id,
        leg.chain_id,
        submitted.tx_hash,
    )

    submitted.wait_to_complete()

    # Continuously check for a package acknowledgement
    # or cancel the arb if the timeout passes
    # Future note: This could be async so other arbs can make
    # progress while this is happening
    def transfer_or_continue() -> None:
        # Check for a package acknowledgement by querying osmosis
        ack_resp = try_multiple_rest_endpoints(
            leg.endpoints,
            (
                f"/ibc/core/channel/v1/channels/{denom_info.channel}/"
                f"ports/{denom_info.port}/packet_acks/"
                f"{submitted.response.events['send_packet']['packet_sequence']}"
            ),
        )

        # Try again
        if not ack_resp:
            return

        # Stop trying, since the transfer succeeded
        schedule.clear()

    schedule.every(IBC_TRANSFER_POLL_INTERVAL_SEC).seconds.until(
        timedelta(seconds=IBC_TRANSFER_TIMEOUT_SEC)
    ).do(transfer_or_continue)

    while schedule.jobs:
        schedule.run_pending()
        time.sleep(1)

    return True


def route_base_denom_profit_quantities(
    base_denom: str,
    starting_amount: int,
    route: List[Union[PoolProvider, AuctionProvider]],
) -> tuple[int, list[int]]:
    """
    Calculates the profit that can be obtained by following the route.
    """

    prev_asset = base_denom
    quantity_received = starting_amount
    quantities: list[int] = []

    for leg in route:
        assets = (
            [leg.asset_a, leg.asset_b]
            if prev_asset == leg.asset_a()
            else [leg.asset_b, leg.asset_a]
        )

        if quantity_received == 0:
            break

        if isinstance(leg, PoolProvider):
            if prev_asset == leg.asset_a():
                quantities.append(int(leg.simulate_swap_asset_a(quantity_received)))
                quantity_received = quantities[-1]
            else:
                quantities.append(int(leg.simulate_swap_asset_b(quantity_received)))
                quantity_received = quantities[-1]
        elif prev_asset == leg.asset_a():
            quantities.append(leg.exchange_rate() * quantity_received)
            quantity_received = quantities[-1]

        prev_asset = assets[1]()

    return (quantity_received - starting_amount, quantities)


def get_routes_with_depth_limit_dfs(
    depth: int,
    limit: int,
    src: str,
    required_leg_types: set[str],
    pools: dict[str, dict[str, List[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
) -> List[List[Union[PoolProvider, AuctionProvider]]]:
    """
    Finds `limit` routes from `src` back to `src` with a maximum route length
    of `depth`.
    """

    # Note: this traversal method works performing depth first search
    # to find `limit` paths of length `depth` that include
    # a valence auction, if `valance_only` is True.

    n_paths = 0
    denom_cache: dict[str, list[DenomChainInfo]] = {}

    def get_routes_from(
        start: str,
        start_chain: str,
        path: List[Union[PoolProvider, AuctionProvider]],
        has_required: set[str],
    ) -> List[List[Union[PoolProvider, AuctionProvider]]]:
        nonlocal limit
        nonlocal denom_cache

        if limit <= 0 or len(path) > depth:
            return []

        # We have arrived at the starting vertex, so no
        # more paths to explore
        if start == src and len(path) == depth:
            if len(required_leg_types - has_required) > 0:
                return []

            limit -= 1

            return [path]

        paths = []

        if f"{start}-{start_chain}" not in denom_cache:
            denom_cache[f"{start}-{start_chain}"] = denom_info(start_chain, start)

        src_denoms = denom_cache[f"{start}-{start_chain}"]

        start_pools: List[Union[AuctionProvider, PoolProvider]] = [
            *(
                pool
                for pool_list in pools.get(start, {}).values()
                for pool in pool_list
            ),
            *auctions.get(start, {}).values(),
            *(
                pool
                for chain_info in src_denoms
                for pool_list in pools.get(chain_info.denom, {}).values()
                for pool in pool_list
            ),
        ]

        for pool in start_pools:
            next_start = pool.asset_a() if pool.asset_a() != start else pool.asset_b()

            paths.extend(
                get_routes_from(
                    next_start,
                    pool.chain_id,
                    path + [pool],
                    has_required | {fmt_route_leg(pool)},
                )
            )

        return paths

    return get_routes_from(src, "neutron-1", [], set())
