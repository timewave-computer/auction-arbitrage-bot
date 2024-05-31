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
from typing import List, Union, Optional, Self, Any, Callable, Iterator, AsyncGenerator
from datetime import datetime, timedelta
import time
from collections import deque
from dataclasses import dataclass
import logging
from src.contracts.route import Leg
from src.contracts.pool.provider import PoolProvider
from src.contracts.pool.osmosis import OsmosisPoolProvider
from src.contracts.pool.astroport import (
    NeutronAstroportPoolProvider,
    asset_info_to_token,
)
from src.contracts.auction import AuctionProvider
from src.scheduler import Ctx
from src.strategies.util import (
    quantities_for_route_profit,
    route_base_denom_profit,
    transfer,
    exec_arb,
    fmt_route,
    fmt_route_debug,
    fmt_route_leg,
    starting_quantity_for_route_profit,
)
from src.util import (
    DenomChainInfo,
    denom_info_on_chain,
    denom_info,
    ContractInfo,
    deployments,
    try_multiple_clients,
    try_multiple_clients_fatal,
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
from aiostream import stream

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


async def strategy(
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

    # Report route stats to user
    logger.info(
        "Finding profitable routes",
    )

    async for quantities, profit, route in listen_routes_with_depth_dfs(
        ctx.cli_args["hops"],
        ctx.cli_args["base_denom"],
        set(ctx.cli_args["require_leg_types"]),
        pools,
        auctions,
        ctx,
    ):
        logger.info("Route queued: %s", fmt_route(route))

        if not ctx.state.balance:
            continue

        logger.info("Executing route with profit of %d: %s", profit, fmt_route(route))

        try:
            await exec_arb(profit, quantities, route, ctx)

            logger.info("Executed route successfully: %s", fmt_route(route))
        except Exception as e:
            logger.error("Arb failed %s: %s", fmt_route(route), e)
        finally:
            ctx = ctx.with_state(ctx.state.poll(ctx, pools, auctions))

    logger.info("Completed arbitrage round")

    return ctx


async def eval_route(
    route: list[Leg],
    ctx: Ctx,
) -> Optional[tuple[list[int], int, list[Leg]]]:
    logger.debug("Evaluating route for profitability: %s", fmt_route(route))

    if not ctx.state.balance:
        logger.error(
            "Failed to fetch bot wallet balance for account %s",
            str(Address(ctx.wallet.public_key(), prefix="neutron")),
        )

        return None

    # First pass heuristic (is it even possible for this route to be
    # profitable)
    profit = await route_base_denom_profit(
        ctx.state.balance,
        route,
    )

    if profit < ctx.cli_args["profit_margin"]:
        logger.debug(
            "Route is not theoretically profitable with profit of %d: %s",
            profit,
            fmt_route_debug(route),
        )

        return None

    logger.debug(
        "Route has theoretical profit of %d: %s",
        profit,
        fmt_route_debug(route),
    )

    starting_amt = await starting_quantity_for_route_profit(ctx.state.balance, route)

    # Second pass: could we reasonably profit from this arb?
    profit, quantities = await quantities_for_route_profit(
        starting_amt,
        route,
    )

    logger.debug("Route %s has execution plan: %s", fmt_route(route), quantities)

    if profit < ctx.cli_args["profit_margin"]:
        logger.debug(
            "Route is not profitable with profit of %d: %s",
            profit,
            fmt_route_debug(route),
        )

        return None

    logger.debug(
        "Route is profitable with realizable profit of %d: %s",
        profit,
        fmt_route_debug(route),
    )

    # Queue the route for execution, since it is profitable
    return (quantities, profit, route)


async def leg_liquidity(leg: Leg) -> tuple[int, int]:
    if isinstance(leg.backend, AuctionProvider):
        return (
            await leg.backend.remaining_asset_b(),
            await leg.backend.remaining_asset_b(),
        )

    return (
        await leg.backend.balance_asset_a(),
        await leg.backend.balance_asset_b(),
    )


async def listen_routes_with_depth_dfs(
    depth: int,
    src: str,
    required_leg_types: set[str],
    pools: dict[str, dict[str, List[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
    ctx: Ctx,
) -> AsyncGenerator[tuple[list[int], int, list[Leg]], None]:
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

    async def next_legs(
        path: list[Leg],
    ) -> AsyncGenerator[tuple[list[int], int, list[Leg]], None]:
        logger.debug("Searching for next leg in path: %s", fmt_route(path))

        nonlocal denom_cache

        if len(path) >= 2:
            assert (
                path[-1].in_asset() == path[-2].out_asset()
                or path[-1].in_asset() in denom_cache[path[-2].out_asset()].values()
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
                len(required_leg_types - set((fmt_route_leg(leg) for leg in path))) > 0
                or len(path) < depth
            ):
                return

            logger.debug(
                "Discovered route with length %d: %s",
                len(path),
                fmt_route(path),
            )

            resp = await eval_route(path, ctx)

            if not resp:
                return

            yield resp

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
            try:
                denom_infos = await denom_info(
                    prev_pool.backend.chain_id, end, ctx.http_session
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
            except asyncio.TimeoutError:
                return

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
                        (pool.asset_a if pool.asset_a() == end else pool.asset_b),
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
                        (pool.asset_a if pool.asset_a() == denom else pool.asset_b),
                        (pool.asset_a if pool.asset_a() != denom else pool.asset_b),
                        pool,
                    )
                    for denom in denom_cache[end].values()
                    for pool_set in pools.get(denom, {}).values()
                    for pool in pool_set
                ),
            }
        )

        routes = stream.merge(*[next_legs(path + [pool]) for pool in next_pools])

        async with routes.stream() as streamer:
            async for route in streamer:
                yield route

    routes = stream.merge(*[next_legs([leg]) for leg in start_legs])

    async with routes.stream() as streamer:
        async for route in streamer:
            yield route
