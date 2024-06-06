"""
Implements an arbitrage strategy with an arbitrary number
of hops using all available providers.
"""

import asyncio
import random
from typing import List, Union, Optional, Self, AsyncGenerator
from dataclasses import dataclass
import logging
from src.contracts.route import Leg, Status, Route
from src.contracts.pool.provider import PoolProvider
from src.contracts.auction import AuctionProvider
from src.scheduler import Ctx
from src.strategies.util import (
    quantities_for_route_profit,
    route_base_denom_profit,
    exec_arb,
    fmt_route,
    fmt_route_debug,
    fmt_route_leg,
    recover_funds,
    IBC_TRANSFER_GAS,
)
from src.util import (
    DenomChainInfo,
    denom_info,
    try_multiple_clients,
)
from cosmpy.crypto.address import Address
from aiostream import stream

logger = logging.getLogger(__name__)


@dataclass
class State:
    """
    A strategy state for a naive strategy that provides caching of
    the route graph.
    """

    balance: Optional[int]
    liquidity_cache: dict[Leg, tuple[int, int]]

    def poll(
        self,
        ctx: Ctx[Self],
        pools: dict[str, dict[str, List[PoolProvider]]],
        auctions: dict[str, dict[str, AuctionProvider]],
    ) -> Self:
        """
        Polls the state for a potential update, leaving the state
        alone, or producing a new state.
        """

        self.liquidity_cache = {}

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
    ctx: Ctx[State],
    pools: dict[str, dict[str, List[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
) -> Ctx[State]:
    """
    Finds new arbitrage opportunities using the context, pools, and auctions.
    """

    state = ctx.state

    if not state:
        ctx.state = State(None, {})
        state = ctx.state

    ctx = ctx.with_state(state.poll(ctx, pools, auctions))

    if ctx.cli_args["cmd"] == "dump":
        return ctx.cancel()

    # Report route stats to user
    logger.info(
        "Finding profitable routes",
    )

    async for r, route in listen_routes_with_depth_dfs(
        ctx.cli_args["hops"],
        ctx.cli_args["base_denom"],
        set(ctx.cli_args["require_leg_types"]),
        pools,
        auctions,
        ctx,
    ):
        ctx.log_route(r, "info", "Route queued: %s", [fmt_route(route)])

        if not state.balance:
            return ctx

        ctx.log_route(
            r,
            "info",
            "Executing route with profit of %d",
            [r.expected_profit],
        )

        try:
            balance_prior = state.balance

            await exec_arb(r, r.expected_profit, r.quantities, route, ctx)

            balance_after_resp = try_multiple_clients(
                ctx.clients["neutron"],
                lambda client: client.query_bank_balance(
                    Address(ctx.wallet.public_key(), prefix="neutron"),
                    ctx.cli_args["base_denom"],
                ),
            )

            if balance_after_resp:
                r.realized_profit = balance_after_resp - balance_prior

            r.status = Status.EXECUTED

            ctx.log_route(r, "info", "Executed route successfully", [])
        except Exception as e:
            ctx.log_route(r, "error", "Arb failed %s: %s", [fmt_route(route), e])

            r.status = Status.FAILED

            terminal_leg_repr = next(
                (leg_repr for leg_repr in r.route if not leg_repr.executed)
            )
            terminal_leg = next(
                (leg for leg in route if str(leg) == str(terminal_leg_repr))
            )

            try:
                await recover_funds(
                    r,
                    terminal_leg,
                    route,
                    ctx,
                )

                r.status = Status.RECOVERED
            except ValueError as e:
                ctx.log_route(r, "error", "Arb recovery failed: %s", [e])

            r.status = Status.FAILED

        ctx.update_route(r)

        ctx = ctx.with_state(state.poll(ctx, pools, auctions)).commit_history()

    logger.info("Completed arbitrage round")

    return ctx


async def eval_route(
    route: list[Leg],
    ctx: Ctx[State],
) -> Optional[tuple[Route, list[Leg]]]:
    r = ctx.queue_route(route, 0, 0, [])

    ctx.log_route(r, "info", "Evaluating route for profitability", [])

    state = ctx.state

    if not state:
        return None

    if not state.balance:
        logger.error(
            "Failed to fetch bot wallet balance for account %s",
            str(Address(ctx.wallet.public_key(), prefix="neutron")),
        )

        return None

    # First pass heuristic (is it even possible for this route to be
    # profitable)
    profit = await route_base_denom_profit(
        state.balance,
        route,
    )

    r.theoretical_profit = profit

    if profit < ctx.cli_args["profit_margin"]:
        logger.debug(
            "Route is not theoretically profitable with profit of %d: %s",
            profit,
            fmt_route_debug(route),
        )

        return None

    ctx.log_route(
        r,
        "info",
        "Route has theoretical profit of %d",
        [
            profit,
        ],
    )

    gas_base_denom = 0

    # Ensure that there is at least 5k of the base chain denom
    # at all times
    if ctx.cli_args["base_denom"] == "untrn":
        gas_base_denom += sum([leg.backend.swap_fee for leg in route])

        for i, leg in enumerate(route[:-1]):
            next_leg = route[i + 1]

            if leg.backend.chain_id != next_leg.backend.chain_id:
                gas_base_denom += IBC_TRANSFER_GAS

    starting_amt = state.balance - gas_base_denom

    ctx.log_route(
        r,
        "info",
        "Route has investment ramp of %d",
        [
            starting_amt,
        ],
    )

    # Second pass: could we reasonably profit from this arb?
    profit, quantities = await quantities_for_route_profit(
        starting_amt,
        route,
        r,
        ctx,
    )

    r.expected_profit = profit
    r.quantities = quantities

    logger.debug("Route has execution plan: %s", r.quantities)

    if profit - gas_base_denom < ctx.cli_args["profit_margin"]:
        ctx.log_route(
            r,
            "info",
            "Route is not profitable with profit of %d",
            [
                profit,
            ],
        )

        return None

    ctx.log_route(
        r,
        "info",
        "Route is profitable with realizable profit of %d",
        [
            profit,
        ],
    )

    # Queue the route for execution, since it is profitable
    return (r, route)


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
    ctx: Ctx[State],
) -> AsyncGenerator[tuple[Route, list[Leg]], None]:
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

    async def next_legs(
        path: list[Leg],
    ) -> AsyncGenerator[tuple[Route, list[Leg]], None]:
        nonlocal denom_cache

        if len(path) >= 2 and not (
            path[-1].in_asset() == path[-2].out_asset()
            or path[-1].in_asset() in denom_cache[path[-2].out_asset()].values()
        ):
            return

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
            if (
                len(required_leg_types - set((fmt_route_leg(leg) for leg in path))) > 0
                or len(path) < depth
            ):
                return

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

        if end not in denom_cache:
            try:
                denom_infos = await denom_info(
                    prev_pool.backend.chain_id,
                    end,
                    ctx.http_session,
                    api_key=ctx.cli_args["skip_api_key"],
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
                        pool.asset_a if pool.asset_a() == end else pool.asset_b,
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
                            if auction.asset_a() == src or auction.asset_a() == denom
                            else auction.asset_b
                        ),
                        (
                            auction.asset_a
                            if auction.asset_a() != src and auction.asset_a() != denom
                            else auction.asset_b
                        ),
                        auction,
                    )
                    for denom in denom_cache[end].values()
                    for auction in auctions.get(denom, {}).values()
                    if auction.chain_id != prev_pool.backend.chain_id
                ),
                *(
                    Leg(
                        (
                            pool.asset_a
                            if pool.asset_a() == src or pool.asset_a() == denom
                            else pool.asset_b
                        ),
                        (
                            pool.asset_a
                            if pool.asset_a() != src and pool.asset_a() != denom
                            else pool.asset_b
                        ),
                        pool,
                    )
                    for denom in denom_cache[end].values()
                    for pool_set in pools.get(denom, {}).values()
                    for pool in pool_set
                    if pool.chain_id != prev_pool.backend.chain_id
                ),
            }
        )
        next_pools = [x for x in next_pools if x not in path]

        if len(next_pools) == 0:
            return

        random.shuffle(next_pools)

        routes = stream.merge(*[next_legs(path + [pool]) for pool in next_pools])

        async with routes.stream() as streamer:
            async for route in streamer:
                yield route

    routes = stream.merge(*[next_legs([leg]) for leg in start_legs])

    async with routes.stream() as streamer:
        async for route in streamer:
            yield route
