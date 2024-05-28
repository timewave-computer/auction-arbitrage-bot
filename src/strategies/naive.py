"""
Implements an arbitrage strategy with an arbitrary number
of hops using all available providers.
"""

import multiprocessing
import multiprocessing.dummy as dummy
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
from src.strategies.util import (
    route_base_denom_profit_quantities,
    route_base_denom_profit,
    transfer,
    exec_arb,
    fmt_route,
    fmt_route_debug,
    fmt_route_leg,
)
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

    balance: Optional[int]

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

        balance_resp = try_multiple_clients(
            ctx.clients["neutron"],
            lambda client: client.query_bank_balance(
                Address(ctx.wallet.public_key(), prefix="neutron"),
                ctx.cli_args["base_denom"],
            ),
        )

        if balance_resp:
            self.balance = balance_resp

        return self


def strategy(
    ctx: Ctx,
    pools: dict[str, dict[str, List[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
) -> Ctx:
    """
    Finds new arbitrage opportunities using the context, pools, and auctions.
    """

    if not ctx.state:
        ctx.state = State(None)

    ctx = ctx.with_state(ctx.state.poll(ctx, pools, auctions))

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

        if not ctx.state.balance:
            continue

        profit = route_base_denom_profit(
            ctx.state.balance,
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

            ctx = ctx.with_state(ctx.state.poll(ctx, pools, auctions))
        except Exception as e:
            logger.error("Arb failed %s: %s", fmt_route(route), e)

    for worker in workers:
        worker.join()

    logger.info("Completed arbitrage round")

    return ctx


def eval_routes(
    in_routes: Queue[List[Leg]],
    out_routes: Queue[List[Leg]],
    ctx: Ctx,
) -> None:
    while True:
        route = in_routes.get()

        logger.debug("Evaluating route for profitability: %s", fmt_route(route))

        if not ctx.state.balance:
            logger.error(
                "Failed to fetch bot wallet balance for account %s",
                str(Address(ctx.wallet.public_key(), prefix="neutron")),
            )

            continue

        profit = route_base_denom_profit(
            ctx.state.balance,
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

                await asyncio.gather(*(next_legs(path + [pool]) for pool in next_pools))

            await asyncio.gather(*[next_legs([leg]) for leg in start_legs])

    asyncio.run(init_searcher())
