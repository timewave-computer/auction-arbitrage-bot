"""
Implements an arbitrage strategy with an arbitrary number
of hops using all available providers.
"""

import multiprocessing
import asyncio
import random
import threading
from queue import Queue
from decimal import Decimal
import json
from typing import List, Union, Optional, Self, Any, Callable
from datetime import datetime, timedelta
import time
from collections import deque
from dataclasses import dataclass
import logging
from src.contracts.leg import Leg
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
    IBC_TRANSFER_TIMEOUT_SEC,
    IBC_TRANSFER_POLL_INTERVAL_SEC,
    DISCOVERY_CONCURRENCY_FACTOR,
    EVALUATION_CONCURRENCY_FACTOR,
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
from schedule import Scheduler
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class State:
    """
    A strategy state for a naive strategy that provides caching of
    the route graph.
    """

    last_discovered: Optional[datetime]
    routes: Optional[List[List[Leg]]]

    def __load_poolfile(
        self,
        ctx: Ctx,
        endpoints: dict[str, dict[str, list[str]]],
    ) -> None:
        """
        Loads routes from the poolfile provided in the --pool_file flag.
        """

        with open(ctx.cli_args["pool_file"], "r", encoding="utf-8") as f:
            poolfile_cts = json.load(f)

            def poolfile_ent_to_leg(ent: dict[str, Any]) -> Leg:
                backend: Optional[Union[PoolProvider, AuctionProvider]] = None

                if "osmosis" in ent:
                    backend = OsmosisPoolProvider(
                        endpoints["osmosis"],
                        ent["osmosis"]["address"],
                        ent["osmosis"]["pool_id"],
                        (ent["osmosis"]["asset_a"], ent["osmosis"]["asset_b"]),
                    )

                if "neutron_astroport" in ent:
                    backend = NeutronAstroportPoolProvider(
                        endpoints["neutron"],
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
                    backend = AuctionProvider(
                        endpoints["neutron"],
                        ContractInfo(
                            deployments()["auctions"]["neutron"],
                            ctx.clients["neutron"],
                            ent["auction"]["address"],
                            "auction",
                        ),
                        ent["auction"]["asset_a"],
                        ent["auction"]["asset_b"],
                    )

                if backend:
                    return Leg(
                        (
                            backend.asset_a
                            if ent["in_asset"] == backend.asset_a()
                            else backend.asset_b
                        ),
                        (
                            backend.asset_a
                            if ent["out_asset"] == backend.asset_a()
                            else backend.asset_b
                        ),
                        backend,
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

        endpoints: dict[str, dict[str, list[str]]] = {
            "neutron": {
                "http": ["https://neutron-rest.publicnode.com"],
                "grpc": ["grpc+https://neutron-grpc.publicnode.com:443"],
            },
            "osmosis": {
                "http": ["https://lcd.osmosis.zone"],
                "grpc": ["grpc+https://osmosis-grpc.publicnode.com:443"],
            },
        }

        if ctx.cli_args["net_config"] is not None:
            with open(ctx.cli_args["net_config"], "r", encoding="utf-8") as f:
                endpoints = json.load(f)

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
            self.routes: List[List[Leg]] = []

        logger.info(
            "Finished building route tree; discovered %d routes",
            len(self.routes),
        )
        self.last_discovered = datetime.now()

        def dump_leg(leg: Leg) -> dict[str, Any]:
            if isinstance(leg.backend, AuctionProvider):
                return {
                    "auction": {
                        "asset_a": leg.backend.asset_a(),
                        "asset_b": leg.backend.asset_b(),
                        "address": leg.backend.contract_info.address,
                    },
                    "in_asset": leg.in_asset(),
                    "out_asset": leg.out_asset(),
                }

            if isinstance(leg.backend, NeutronAstroportPoolProvider):
                return {
                    "neutron_astroport": leg.backend.dump(),
                    "in_asset": leg.in_asset(),
                    "out_asset": leg.out_asset(),
                }

            if isinstance(leg.backend, OsmosisPoolProvider):
                return {
                    "osmosis": leg.backend.dump(),
                    "in_asset": leg.in_asset(),
                    "out_asset": leg.out_asset(),
                }

            raise ValueError("Invalid route leg type.")

        # The user wants to dump discovered routes, so we can exit now
        if ctx.cli_args["cmd"] == "dump":
            with open(ctx.cli_args["pool_file"], "r+", encoding="utf-8") as f:
                poolfile_cts = json.load(f)
                poolfile_cts["routes"] = [
                    [dump_leg(pool) for pool in route] for route in self.routes
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

    if ctx.cli_args["cmd"] == "dump":
        return ctx.cancel()

    workers = []

    # Routes to evaluate for profitability, and routes to execute
    to_eval: Queue[List[Leg]] = Queue(maxsize=0)
    to_exec: Queue[List[Leg]] = Queue(maxsize=0)

    # Report route stats to user
    logger.info(
        "Finding profitable routes",
    )

    workers.append(
        threading.Thread(
            target=listen_routes_with_depth_dfs,
            args=[
                to_eval,
                ctx.cli_args["hops"],
                ctx.cli_args["base_denom"],
                set(ctx.cli_args["require_leg_types"]),
                pools,
                auctions,
            ],
        )
    )
    workers[-1].start()

    # Evaluate up to util/EVALUATION_CONCURRENCY_FACTOR
    # arbs at the same time for profitability (based
    # and optimizatoinmaxxing pilled)
    for _ in range(EVALUATION_CONCURRENCY_FACTOR):
        workers.append(
            threading.Thread(
                target=eval_routes,
                args=[
                    to_eval,
                    to_exec,
                    ctx,
                ],
            )
        )
        workers[-1].start()

    while True:
        route = to_exec.get()

        logger.debug("Route queued: %s", fmt_route(route))

        balance_resp = try_multiple_clients(
            ctx.clients["neutron"],
            lambda client: client.query_bank_balance(
                Address(ctx.wallet.public_key(), prefix="neutron"),
                ctx.cli_args["base_denom"],
            ),
        )

        if not balance_resp:
            continue

        profit, _ = route_base_denom_profit_quantities(
            balance_resp,
            route,
        )

        if profit < ctx.cli_args["profit_margin"]:
            logger.debug(
                "Route is not profitable with profit of %d: %s",
                profit,
                fmt_route(route),
            )

            continue

        logger.info("Executing route with profit of %d: %s", profit, fmt_route(route))

        try:
            exec_arb(route, ctx)
        except Exception as e:
            logger.error("Arb failed %s: %s", fmt_route(route), e)

    for worker in workers:
        worker.join()

    logger.info("Completed arbitrage round")

    return ctx


def fmt_route_leg(leg: Leg) -> str:
    """
    Returns the nature of the route leg (i.e., "osmosis," "astroport," or, "valence.")
    """

    if isinstance(leg.backend, OsmosisPoolProvider):
        return "osmosis"

    if isinstance(leg.backend, NeutronAstroportPoolProvider):
        return "astroport"

    if isinstance(leg.backend, AuctionProvider):
        return "valence"

    return leg.backend.kind


def fmt_route(route: list[Leg]) -> str:
    return " -> ".join(
        map(
            lambda route_leg: fmt_route_leg(route_leg)
            + ": "
            + route_leg.in_asset()
            + " - "
            + route_leg.out_asset(),
            route,
        )
    )


def fmt_route_debug(route: list[Leg]) -> str:
    return " -> ".join(
        map(
            lambda route_leg: f"{fmt_route_leg(route_leg)}: {route_leg.in_asset()} - {route_leg.out_asset()} @ {route_leg.backend.pool_id if isinstance(route_leg.backend, OsmosisPoolProvider) else (route_leg.backend.contract_info.address if isinstance(route_leg.backend, NeutronAstroportPoolProvider) or isinstance(route_leg.backend, AuctionProvider) else '')}",
            route,
        )
    )


def exec_arb(
    route: List[Leg],
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
        swap_balance,
        route,
    )

    prev_leg: Optional[Leg] = None

    # Submit arb trades for all profitable routes
    logger.info(
        ("Queueing candidate arbitrage opportunity with " "route with %d hop(s): %s"),
        len(route),
        fmt_route(route),
    )

    for leg in route:
        logger.info(
            "Queueing arb leg on %s with %s -> %s",
            fmt_route_leg(leg),
            leg.in_asset(),
            leg.out_asset(),
        )

        tx: Optional[SubmittedTx] = None

        prev_asset_info: Optional[DenomChainInfo] = None

        if prev_leg:

            async def get_denom_info() -> Optional[DenomChainInfo]:
                async with aiohttp.ClientSession() as session:
                    return await denom_info_on_chain(
                        prev_leg.backend.chain_id,
                        prev_leg.out_asset(),
                        leg.backend.chain_id,
                        session,
                    )

            prev_asset_info = asyncio.run(get_denom_info())

        to_swap = (
            swap_balance
            if not prev_leg
            else try_multiple_clients_fatal(
                ctx.clients[prev_leg.backend.chain_id.split("-")[0]],
                lambda client: client.query_bank_balance(
                    Address(
                        ctx.wallet.public_key(), prefix=prev_leg.backend.chain_prefix
                    ),
                    prev_leg.out_asset(),
                ),
            )
        )

        logger.info(
            "Executing arb leg on %s with %d %s -> %s",
            fmt_route_leg(leg),
            to_swap,
            leg.in_asset(),
            leg.out_asset(),
        )

        # The funds are not already on the current chain, so they need to be moved
        if prev_leg and prev_leg.backend.chain_id != leg.backend.chain_id:
            logger.info(
                "Transfering %s %s from %s -> %s",
                to_swap,
                prev_leg.out_asset(),
                prev_leg.backend.chain_id,
                leg.backend.chain_id,
            )

            # Ensure that there is at least 5k of the base chain denom
            # at all times
            if prev_leg.out_asset() == prev_leg.backend.chain_fee_denom:
                to_swap -= 5000

            # Cancel arb if the transfer fails
            try:
                transfer(prev_leg.out_asset(), prev_leg, leg, ctx, to_swap)
            except Exception as e:
                logger.error(
                    "Failed to transfer funds from %s -> %s: %s",
                    prev_leg.backend.chain_id,
                    leg.backend.chain_id,
                    e,
                )

                return

            logger.info(
                "Transfer succeeded: %s -> %s",
                prev_leg.backend.chain_id,
                leg.backend.chain_id,
            )

            time.sleep(IBC_TRANSFER_POLL_INTERVAL_SEC)

            to_swap = try_multiple_clients_fatal(
                ctx.clients[leg.backend.chain_id.split("-")[0]],
                lambda client: client.query_bank_balance(
                    Address(ctx.wallet.public_key(), prefix=leg.backend.chain_prefix),
                    leg.in_asset(),
                ),
            )

            logger.debug(
                "Balance to swap for %s on %s: %d",
                str(Address(ctx.wallet.public_key(), prefix=leg.backend.chain_prefix)),
                leg.backend.chain_id,
                to_swap,
            )
        else:
            logger.info("Arb leg can be executed atomically; no transfer necessary")

        # Ensure that there is at least 5k of the base chain denom
        # at all times
        if leg.in_asset() == leg.backend.chain_fee_denom:
            to_swap -= 5000

        # If the arb leg is on astroport, simply execute the swap
        # on asset A, producing asset B
        if isinstance(leg.backend, PoolProvider):
            to_receive = (
                leg.backend.simulate_swap_asset_a(to_swap)
                if leg.in_asset == leg.backend.asset_a
                else leg.backend.simulate_swap_asset_b(to_swap)
            )

            if isinstance(leg.backend, NeutronAstroportPoolProvider):
                logger.info(
                    "Submitting arb to contract: %s", leg.backend.contract_info.address
                )

            if isinstance(leg.backend, OsmosisPoolProvider):
                logger.info("Submitting arb to pool: %d", leg.backend.pool_id)

            # Submit the arb trade
            if leg.in_asset == leg.backend.asset_a:
                tx = leg.backend.swap_asset_a(
                    ctx.wallet,
                    to_swap,
                    to_receive,
                ).wait_to_complete()
            else:
                tx = leg.backend.swap_asset_b(
                    ctx.wallet,
                    to_swap,
                    to_receive,
                ).wait_to_complete()

        if (
            isinstance(leg.backend, AuctionProvider)
            and leg.in_asset == leg.backend.asset_a
        ):
            tx = leg.backend.swap_asset_a(ctx.wallet, to_swap).wait_to_complete()

        if tx:
            # Notify the user of the arb trade
            logger.info(
                "Executed leg %s -> %s: %s",
                leg.in_asset(),
                leg.out_asset(),
                tx.tx_hash,
            )

        prev_leg = leg


def transfer(
    denom: str,
    prev_leg: Leg,
    leg: Leg,
    ctx: Ctx,
    swap_balance: int,
) -> None:
    """
    Synchronously executes an IBC transfer from one leg in an arbitrage
    trade to the next, moving `swap_balance` of the asset_b in the source
    leg to asset_a in the destination leg. Returns true if the transfer
    succeeded.
    """

    async def get_denom_info() -> Optional[DenomChainInfo]:
        async with aiohttp.ClientSession() as session:
            return await denom_info_on_chain(
                src_chain=prev_leg.backend.chain_id,
                src_denom=denom,
                dest_chain=leg.backend.chain_id,
                session=session,
            )

    denom_info = asyncio.run(get_denom_info())

    if not denom_info:
        raise ValueError("Missing denom info for target chain in IBC transfer")

    channel_info = try_multiple_rest_endpoints(
        ctx.endpoints[leg.backend.chain_id.split("-")[0]]["http"],
        f"/ibc/core/channel/v1/channels/{denom_info.channel}/ports/{denom_info.port}",
    )

    if not channel_info:
        raise ValueError("Missing channel info for target chain in IBC transfer")

    # Not enough info to complete the transfer
    if not denom_info or not denom_info.port or not denom_info.channel:
        raise ValueError("Missing channel info for target chain in IBC transfer")

    acc = try_multiple_clients_fatal(
        ctx.clients[prev_leg.backend.chain_id.split("-")[0]],
        lambda client: client.query_account(
            str(Address(ctx.wallet.public_key(), prefix=prev_leg.backend.chain_prefix))
        ),
    )

    logger.debug(
        "Executing IBC transfer %s from %s -> %s with source port %s, source channel %s, sender %s, and receiver %s",
        denom,
        prev_leg.backend.chain_id,
        leg.backend.chain_id,
        channel_info["channel"]["counterparty"]["port_id"],
        channel_info["channel"]["counterparty"]["channel_id"],
        str(Address(ctx.wallet.public_key(), prefix=prev_leg.backend.chain_prefix)),
        str(Address(ctx.wallet.public_key(), prefix=leg.backend.chain_prefix)),
    )

    # Create a messate transfering the funds
    msg = tx_pb2.MsgTransfer(  # pylint: disable=no-member
        source_port=channel_info["channel"]["counterparty"]["port_id"],
        source_channel=channel_info["channel"]["counterparty"]["channel_id"],
        sender=str(
            Address(ctx.wallet.public_key(), prefix=prev_leg.backend.chain_prefix)
        ),
        receiver=str(Address(ctx.wallet.public_key(), prefix=leg.backend.chain_prefix)),
        timeout_timestamp=time.time_ns() + 600 * 10**9,
    )
    msg.token.CopyFrom(
        coin_pb2.Coin(  # pylint: disable=maybe-no-member
            denom=denom, amount=str(swap_balance)
        )
    )

    tx = Transaction()
    tx.add_message(msg)
    tx.seal(
        SigningCfg.direct(ctx.wallet.public_key(), acc.sequence),
        f"3000{prev_leg.backend.chain_fee_denom}",
        50000,
    )
    tx.sign(ctx.wallet.signer(), prev_leg.backend.chain_id, acc.number)
    tx.complete()

    submitted = try_multiple_clients_fatal(
        ctx.clients[prev_leg.backend.chain_id.split("-")[0]],
        lambda client: client.broadcast_tx(tx),
    ).wait_to_complete()

    logger.info(
        "Submitted IBC transfer from src %s to %s: %s",
        prev_leg.backend.chain_id,
        leg.backend.chain_id,
        submitted.tx_hash,
    )

    # Continuously check for a package acknowledgement
    # or cancel the arb if the timeout passes
    # Future note: This could be async so other arbs can make
    # progress while this is happening
    def transfer_or_continue() -> bool:
        logger.info("Checking IBC transfer status %s", submitted.tx_hash)

        # Check for a package acknowledgement by querying osmosis
        ack_resp = try_multiple_rest_endpoints(
            leg.backend.endpoints,
            (
                f"/ibc/core/channel/v1/channels/{denom_info.channel}/"
                f"ports/{denom_info.port}/packet_acks/"
                f"{submitted.response.events['send_packet']['packet_sequence']}"
            ),
        )

        # Try again
        if not ack_resp:
            logger.info(
                "IBC transfer %s has not yet completed; waiting...", submitted.tx_hash
            )

            return False

        # Stop trying, since the transfer succeeded
        return True

    timeout = time.time() + IBC_TRANSFER_TIMEOUT_SEC

    while time.time() < timeout:
        time.sleep(IBC_TRANSFER_POLL_INTERVAL_SEC)

        if transfer_or_continue():
            break


def route_base_denom_profit_quantities(
    starting_amount: int,
    route: List[Leg],
) -> tuple[int, list[int]]:
    """
    Calculates the profit that can be obtained by following the route.
    """

    if len(route) == 0:
        return (0, [])

    quantities: list[int] = [starting_amount]

    for leg in route:
        if quantities[-1] == 0:
            break

        prev_amt = min(
            quantities[-1],
            (
                leg.backend.remaining_asset_b()
                if isinstance(leg.backend, AuctionProvider)
                else (
                    leg.backend.balance_asset_a()
                    if leg.in_asset == leg.backend.asset_a
                    else leg.backend.balance_asset_b()
                )
            ),
        )

        if isinstance(leg.backend, AuctionProvider):
            quantities.append(leg.backend.exchange_rate() * prev_amt)

            continue

        if leg.in_asset == leg.backend.asset_a:
            quantities.append(int(leg.backend.simulate_swap_asset_a(prev_amt)))

            continue

        quantities.append(int(leg.backend.simulate_swap_asset_b(prev_amt)))

    print(quantities, route[-1].out_asset(), route[0].in_asset())

    return (quantities[-1] - quantities[0], quantities)


def eval_routes(
    in_routes: Queue[List[Leg]], out_routes: Queue[List[Leg]], ctx: Ctx
) -> None:
    while True:
        route = in_routes.get()

        logger.debug("Evaluating route for profitability: %s", fmt_route(route))

        balance_resp = try_multiple_clients(
            ctx.clients["neutron"],
            lambda client: client.query_bank_balance(
                Address(ctx.wallet.public_key(), prefix="neutron"),
                ctx.cli_args["base_denom"],
            ),
        )

        if not balance_resp:
            logger.error(
                "Failed to fetch bot wallet balance for account %s",
                str(Address(ctx.wallet.public_key(), prefix="neutron")),
            )

            continue

        profit, _ = route_base_denom_profit_quantities(
            balance_resp,
            route,
        )

        if profit < ctx.cli_args["profit_margin"]:
            logger.debug(
                "Route is not profitable with profit of %d: %s",
                profit,
                fmt_route_debug(route),
            )

            continue

        # Queue the route for execution, since it is profitable
        out_routes.put(route)


def leg_liquidity(leg: Leg) -> tuple[int, int]:
    if isinstance(leg.backend, AuctionProvider):
        return (leg.backend.remaining_asset_b(), leg.backend.remaining_asset_b())

    return (
        leg.backend.balance_asset_a(),
        leg.backend.balance_asset_b(),
    )


def listen_routes_with_depth_dfs(
    routes: Queue[List[Leg]],
    depth: int,
    src: str,
    required_leg_types: set[str],
    pools: dict[str, dict[str, List[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
) -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())

    denom_cache: dict[str, dict[str, str]] = {}

    start_pools: list[Union[AuctionProvider, PoolProvider]] = [
        *auctions.get(src, {}).values(),
        *(pool for pool_set in pools.get(src, {}).values() for pool in pool_set),
    ]

    start_legs: list[Leg] = [
        Leg(
            pool.asset_a if pool.asset_a() == src else pool.asset_b,
            pool.asset_b if pool.asset_a() == src else pool.asset_a,
            pool,
        )
        for pool in start_pools
    ]

    random.shuffle(start_legs)
    start_legs = sorted(start_legs, key=leg_liquidity, reverse=True)

    async def init_searcher() -> None:
        sem = asyncio.Semaphore(DISCOVERY_CONCURRENCY_FACTOR)

        async with aiohttp.ClientSession() as session:

            async def next_legs(path: list[Leg]) -> None:
                nonlocal denom_cache
                nonlocal routes

                if len(path) >= 2:
                    assert (
                        path[-1].in_asset() == path[-2].out_asset()
                        or path[-1].in_asset()
                        in denom_cache[path[-2].out_asset()].values()
                    )

                # Only find `limit` pools
                # with a depth less than `depth
                if len(path) > depth:
                    return

                # We must be finding the next leg
                # in an already existing path
                assert len(path) > 0

                prev_pool = path[-1]

                # This leg **must** be connected to the previous
                # by construction, so therefore, if any
                # of the denoms match the starting denom, we are
                # finished, and the circuit is closed
                if len(path) > 1 and src == prev_pool.out_asset():
                    logger.debug(
                        "Circuit closed with length %d: %s",
                        len(path),
                        fmt_route(path),
                    )

                    if (
                        len(
                            required_leg_types
                            - set((fmt_route_leg(leg) for leg in path))
                        )
                        > 0
                        or len(path) < depth
                    ):
                        return

                    logger.debug(
                        "Discovered route with length %d: %s",
                        len(path),
                        fmt_route(path),
                    )

                    routes.put(path)

                    return

                # Find all pools that start where this leg ends
                # the "ending" point of this leg is defined
                # as its denom which is not shared by the
                # leg before it.

                # note: the previous leg's denoms will
                # be in the denom cache by construction
                # if this is the first leg, then there is
                # no more work to do
                end = prev_pool.out_asset()

                if not end in denom_cache:
                    async with sem:
                        denom_infos = await denom_info(
                            prev_pool.backend.chain_id, end, session
                        )

                        denom_cache[end] = {
                            info.chain_id: info.denom
                            for info in (
                                denom_infos
                                + [
                                    DenomChainInfo(
                                        denom=end,
                                        port=None,
                                        channel=None,
                                        chain_id=prev_pool.backend.chain_id,
                                    )
                                ]
                            )
                            if info.chain_id
                        }

                # A pool is a candidate to be a next pool if it has a denom
                # contained in denom_cache[end] or one of its denoms *is* end
                next_pools: list[Leg] = list(
                    {
                        # Atomic pools
                        *(
                            Leg(
                                (
                                    auction.asset_a
                                    if auction.asset_a() == end
                                    else auction.asset_b
                                ),
                                (
                                    auction.asset_a
                                    if auction.asset_a() != end
                                    else auction.asset_b
                                ),
                                auction,
                            )
                            for auction in auctions.get(end, {}).values()
                        ),
                        *(
                            Leg(
                                (
                                    pool.asset_a
                                    if pool.asset_a() == end
                                    else pool.asset_b
                                ),
                                pool.asset_a if pool.asset_a() != end else pool.asset_b,
                                pool,
                            )
                            for pool_set in pools.get(end, {}).values()
                            for pool in pool_set
                        ),
                        # IBC pools
                        *(
                            Leg(
                                (
                                    auction.asset_a
                                    if auction.asset_a() == denom
                                    else auction.asset_b
                                ),
                                (
                                    auction.asset_a
                                    if auction.asset_a() != denom
                                    else auction.asset_b
                                ),
                                auction,
                            )
                            for denom in denom_cache[end].values()
                            for auction in auctions.get(denom, {}).values()
                        ),
                        *(
                            Leg(
                                (
                                    pool.asset_a
                                    if pool.asset_a() == denom
                                    else pool.asset_b
                                ),
                                (
                                    pool.asset_a
                                    if pool.asset_a() != denom
                                    else pool.asset_b
                                ),
                                pool,
                            )
                            for denom in denom_cache[end].values()
                            for pool_set in pools.get(denom, {}).values()
                            for pool in pool_set
                        ),
                    }
                )
                random.shuffle(next_pools)

                by_liquidity = map(leg_liquidity, next_pools)
                key: Callable[[tuple[Leg, tuple[int, int]]], tuple[int, int]] = (
                    lambda pool_liquidity: pool_liquidity[1]
                )

                # Order next pools by liquidity
                sorted_pools = (
                    pool_liquidity[0]
                    for pool_liquidity in sorted(
                        zip(next_pools, by_liquidity),
                        key=key,
                        reverse=True,
                    )
                )

                await asyncio.gather(
                    *(next_legs(path + [pool]) for pool in sorted_pools)
                )

            await asyncio.gather(*[next_legs([leg]) for leg in start_legs])

    asyncio.run(init_searcher())
