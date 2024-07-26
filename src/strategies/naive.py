"""
Implements an arbitrage strategy with an arbitrary number
of hops using all available providers.
"""

from asyncio import Task
from queue import Queue
import asyncio
import traceback
from typing import List, Optional, Self, AsyncGenerator, Any
from dataclasses import dataclass
import logging
from src.contracts.route import Leg, Status, Route
from src.contracts.pool.provider import PoolProvider
from src.contracts.auction import AuctionProvider
from src.scheduler import Ctx
from src.strategies.util import (
    rebalance_portfolio,
    listen_routes_with_depth_dfs,
    quantities_for_route_profit,
    route_base_denom_profit,
    exec_arb,
    fmt_route,
    fmt_route_debug,
    recover_funds,
    IBC_TRANSFER_GAS,
)
from src.util import (
    try_multiple_clients,
)
from cosmpy.crypto.address import Address

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
        ctx: Ctx[Self],
        pools: dict[str, dict[str, List[PoolProvider]]],
        auctions: dict[str, dict[str, AuctionProvider]],
    ) -> Self:
        """
        Polls the state for a potential update, leaving the state
        alone, or producing a new state.
        """

        balance_resp = try_multiple_clients(
            ctx.clients[list(ctx.deployments["auctions"].keys())[0]],
            lambda client: client.query_bank_balance(
                Address(
                    ctx.wallet.public_key(),
                    prefix=list(ctx.deployments["auctions"].values())[0][
                        "chain_prefix"
                    ],
                ),
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
        ctx.state = State(None)
        state = ctx.state

    ctx = ctx.with_state(state.poll(ctx, pools, auctions))

    if ctx.cli_args["cmd"] == "dump":
        return ctx.cancel()

    logger.info("Rebalancing portfolio")

    try:
        await rebalance_portfolio(pools, auctions, ctx)
    except ValueError as e:
        logger.error("Failed to rebalance portfolio: %s", e)

    # Report route stats to user
    logger.info(
        "Finding profitable routes",
    )

    async for r, route in eval_routes(
        listen_routes_with_depth_dfs(
            ctx.cli_args["hops"],
            ctx.cli_args["base_denom"],
            ctx.cli_args["base_denom"],
            set(ctx.cli_args["require_leg_types"]),
            pools,
            auctions,
            ctx,
        ),
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
                ctx.clients[list(ctx.deployments["auctions"].keys())[0]],
                lambda client: client.query_bank_balance(
                    Address(
                        ctx.wallet.public_key(),
                        prefix=list(ctx.deployments["auctions"].values())[0][
                            "chain_prefix"
                        ],
                    ),
                    ctx.cli_args["base_denom"],
                ),
            )

            if balance_after_resp:
                r.realized_profit = balance_after_resp - balance_prior

            if r.route[-1].executed:
                r.status = Status.EXECUTED

                ctx.log_route(r, "info", "Executed route successfully", [])
                ctx.log_route(
                    r, "info", "Route executed with profit %d", [r.realized_profit]
                )
            else:
                ctx.log_route(r, "info", "Route aborted", [])
        except Exception:
            ctx.log_route(
                r,
                "error",
                "Arb failed %s: %s",
                [
                    fmt_route(route),
                    traceback.format_exc().replace("\n", f"\n{r.uid}- Arb failed: "),
                ],
            )

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

        ctx = ctx.with_state(state.poll(ctx, pools, auctions))

    ctx.commit_history()

    logger.info("Completed arbitrage round")

    return ctx


def route_gas(route: list[Leg]) -> int:
    """
    Estimates the gas required to execute a route,
    given that the base denom is untrn.
    """

    gas = 0

    # Ensure that there is at least 5k of the base chain denom
    # at all times
    gas += int(sum((leg.backend.swap_fee for leg in route)))

    for i, leg in enumerate(route[:-1]):
        next_leg = route[i + 1]

        if leg.backend.chain_id != next_leg.backend.chain_id:
            gas += IBC_TRANSFER_GAS

    return gas


async def eval_route(
    r: Route,
    route: list[Leg],
    ctx: Ctx[State],
) -> Optional[tuple[Route, list[Leg]]]:
    ctx.log_route(
        r, "info", "Evaluating route for profitability: %s", [fmt_route(route)]
    )

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

    gas_base_denom = 0 if ctx.cli_args["base_denom"] != "untrn" else route_gas(route)

    ctx.log_route(
        r,
        "info",
        "Route will cost %d to execute",
        [
            gas_base_denom,
        ],
    )

    starting_amt = state.balance - gas_base_denom

    ctx.log_route(
        r,
        "info",
        "Route has starting amount of %d",
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


async def eval_routes(
    listened_routes: AsyncGenerator[tuple[Route, list[Leg]], None],
    ctx: Ctx[Any],
) -> AsyncGenerator[tuple[Route, list[Leg]], None]:
    """ "
    Evaluates routes concurrently, yielding profitable routes.
    """

    tasks = set()
    profitable_routes: Queue[tuple[Route, list[Leg]]] = Queue()

    async for r, route in listened_routes:
        while not profitable_routes.empty():
            yield profitable_routes.get()

        task = asyncio.create_task(eval_route(r, route, ctx))

        def eval_end(t: Task[Optional[tuple[Route, list[Leg]]]]) -> None:
            tasks.discard(task)

            res = t.result()

            if not res:
                return

            profitable_routes.put(res)

        task.add_done_callback(eval_end)
        tasks.add(task)
