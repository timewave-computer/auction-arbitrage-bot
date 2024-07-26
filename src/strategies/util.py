"""
Defines common utilities shared across arbitrage strategies.
"""

import random
import traceback
import asyncio
from itertools import groupby
import json
from decimal import Decimal
import operator
from functools import reduce
import logging
import time
from typing import Optional, Any, Iterator, AsyncGenerator, Union
from src.contracts.route import Leg, Route
from src.contracts.auction import AuctionProvider
from src.contracts.pool.provider import PoolProvider
from src.contracts.pool.osmosis import OsmosisPoolProvider
from src.contracts.pool.astroport import (
    NeutronAstroportPoolProvider,
)
from src.util import (
    chain_info,
    IBC_TRANSFER_TIMEOUT_SEC,
    IBC_TRANSFER_POLL_INTERVAL_SEC,
    try_multiple_rest_endpoints,
    try_multiple_clients_fatal,
    try_multiple_clients,
    DENOM_QUANTITY_ABORT_ARB,
    denom_route,
    denom_info_on_chain,
    denom_info,
    DenomChainInfo,
)
from src.scheduler import Ctx
from cosmos.base.v1beta1 import coin_pb2
from cosmpy.crypto.address import Address
from cosmpy.aerial.tx import Transaction, SigningCfg
from cosmpy.aerial.tx_helpers import SubmittedTx
from cosmpy.aerial.wallet import LocalWallet
from ibc.applications.transfer.v1 import tx_pb2
from aiostream import stream

logger = logging.getLogger(__name__)


MAX_POOL_LIQUIDITY_TRADE = Decimal("0.1")

"""
The amount of the summed gas limit that will be consumed if messages
are batched together.
"""
GAS_DISCOUNT_BATCHED = Decimal("0.9")


IBC_TRANSFER_GAS = 5000


def fmt_route_leg(leg: Leg) -> str:
    """
    Returns the nature of the route leg (i.e., "osmosis," "astroport," or, "auction.")
    """

    if isinstance(leg.backend, OsmosisPoolProvider):
        return "osmosis"

    if isinstance(leg.backend, NeutronAstroportPoolProvider):
        return f"astroport ({leg.backend.chain_id})"

    if isinstance(leg.backend, AuctionProvider):
        return "auction"

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


def collapse_route(
    route_legs_quantities: Iterator[tuple[Leg, int]]
) -> list[list[tuple[Leg, int]]]:
    """
    Groups legs of the route by common consecutive chain ID's, meaning they can be
    exceuted atomically.
    """

    return [
        list(sublegs)
        for _, sublegs in groupby(
            route_legs_quantities, lambda r_q: r_q[0].backend.chain_id
        )
    ]


def build_atomic_arb(
    sublegs: list[tuple[Leg, int]], wallet: LocalWallet
) -> Transaction:
    """
    Creates a transaction whose messages are the arb trades
    in an atomic arb.
    """

    msgs = (
        (
            leg.backend.swap_msg_asset_b(wallet, to_swap, 0)
            if leg.in_asset == leg.backend.asset_b
            and not isinstance(leg.backend, AuctionProvider)
            else leg.backend.swap_msg_asset_a(wallet, to_swap, 0)
        )
        for leg, to_swap in sublegs
    )

    tx = Transaction()

    for msg in msgs:
        tx.add_message(msg)

    return tx


async def exec_arb(
    route_ent: Route,
    profit: int,
    quantities: list[int],
    route: list[Leg],
    ctx: Ctx[Any],
) -> None:
    """
    Executes a list of arbitrage trades composed of multiple hops.

    Takes a list of arbitrage trades, and a list of prices for each hop
    in each trade.
    """

    logger.debug("Route %s has execution plan: %s", fmt_route(route), quantities)

    if len(quantities) < len(route):
        raise ValueError(
            f"Insufficient execution planning for route {fmt_route(route)}; canceling"
        )

    if profit < ctx.cli_args["profit_margin"]:
        ctx.log_route(
            route_ent, "error", "Insufficient realized profit for route; canceling", []
        )

        return

    prev_leg: Optional[Leg] = None

    # Submit arb trades for all profitable routes
    ctx.log_route(
        route_ent,
        "info",
        "Queueing candidpate arbitrage opportunity with route with %d hop(s): %s",
        [len(route), fmt_route(route)],
    )

    to_execute: list[list[tuple[Leg, int]]] = collapse_route(zip(route, quantities))

    ctx.log_route(
        route_ent,
        "info",
        "Queueing candidpate arbitrage opportunity with atomized execution plan: %s",
        [
            [fmt_route([leg for leg, _ in sublegs]) for sublegs in to_execute],
        ],
    )

    for sublegs in to_execute:
        leg_to_swap: tuple[Leg, int] = sublegs[0]
        (leg, to_swap) = leg_to_swap

        # Log legs on the same chain
        if len(sublegs) > 1:
            ctx.log_route(
                route_ent,
                "info",
                "%d legs are atomic and will be executed in one tx: %s",
                [len(sublegs), fmt_route([leg for (leg, to_swap) in sublegs])],
            )

        ctx.log_route(
            route_ent,
            "info",
            "Queueing arb legs: [%s]",
            [fmt_route([leg for leg, _ in sublegs])],
        )

        tx: Optional[SubmittedTx] = None

        ctx.log_route(
            route_ent,
            "info",
            "Executing arb legs: [%s]",
            [
                ", ".join(
                    (
                        f"{fmt_route_leg(leg)} with {to_swap} {leg.in_asset()}"
                        for (leg, to_swap) in sublegs
                    )
                )
            ],
        )

        # The funds are not already on the current chain, so they need to be moved
        if prev_leg and prev_leg.backend.chain_id != leg.backend.chain_id:
            ctx.log_route(
                route_ent,
                "info",
                "Transfering %s %s from %s -> %s",
                [
                    to_swap,
                    prev_leg.out_asset(),
                    prev_leg.backend.chain_id,
                    leg.backend.chain_id,
                ],
            )

            # Cancel arb if the transfer fails
            try:
                await transfer(
                    route_ent,
                    prev_leg.out_asset(),
                    prev_leg,
                    leg,
                    ctx,
                    to_swap,
                )
            except Exception:
                ctx.log_route(
                    route_ent,
                    "error",
                    "Arb failed - failed to transfer funds from %s -> %s: %s",
                    [
                        prev_leg.backend.chain_id,
                        leg.backend.chain_id,
                        traceback.format_exc().replace(
                            "\n",
                            f"\n{route_ent.uid}- Arb failed - failed to transfer funds: ",
                        ),
                    ],
                )

                return

            ctx.log_route(
                route_ent,
                "info",
                "Transfer succeeded: %s -> %s",
                [prev_leg.backend.chain_id, leg.backend.chain_id],
            )

            time.sleep(IBC_TRANSFER_POLL_INTERVAL_SEC)

            logger.debug(
                "Balance to swap for %s on %s: %d",
                str(Address(ctx.wallet.public_key(), prefix=leg.backend.chain_prefix)),
                leg.backend.chain_id,
                to_swap,
            )
        else:
            ctx.log_route(
                route_ent,
                "info",
                "Arb leg(s) can be executed on the same chain; no transfer necessary",
                [],
            )

        if to_swap < 0:
            ctx.log_route(
                route_ent,
                "info",
                "Arb is not profitable including gas costs; aborting",
                [],
            )

            return

        # If there are multiple legs, build them as one large
        # transaction
        if len(sublegs) > 1:
            tx = build_atomic_arb(sublegs, ctx.wallet)

            acc = try_multiple_clients_fatal(
                ctx.clients[leg.backend.chain_id],
                lambda client: client.query_account(
                    str(
                        Address(
                            ctx.wallet.public_key(),
                            prefix=leg.backend.chain_prefix,
                        )
                    )
                ),
            )

            ctx.log_route(route_ent, "info", "Built arb message chain", [])

            gas_limit = int(
                (
                    sum((leg.backend.swap_gas_limit for leg, _ in sublegs))
                    * GAS_DISCOUNT_BATCHED
                )
            )
            gas = int(
                gas_limit * sum((leg.backend.chain_gas_price for leg, _ in sublegs))
            )

            tx.seal(
                signing_cfgs=SigningCfg.direct(ctx.wallet.public_key(), acc.sequence),
                gas_limit=gas_limit,
                fee=f"{gas}{leg.backend.chain_fee_denom}",
            )
            tx.sign(ctx.wallet.signer(), leg.backend.chain_id, acc.number)
            tx.complete()

            ctx.log_route(
                route_ent,
                "info",
                "Submitting arb",
                [],
            )

            tx = leg.backend.submit_swap_tx(tx).wait_to_complete()

            # Notify the user of the arb trade
            ctx.log_route(
                route_ent,
                "info",
                "Executed legs %s: %s",
                [
                    ", ".join(
                        (
                            f"{fmt_route_leg(leg)} with {to_swap} {leg.in_asset()}"
                            for (leg, to_swap) in sublegs
                        )
                    ),
                    tx.tx_hash,
                ],
            )

            for leg, _ in sublegs:
                next(
                    (
                        leg_repr
                        for leg_repr in route_ent.route
                        if str(leg_repr) == str(leg)
                    )
                ).executed = True

                prev_leg = leg

            continue

        # If the arb leg is on astroport, simply execute the swap
        # on asset A, producing asset B
        if isinstance(leg.backend, PoolProvider):
            to_receive = (
                await leg.backend.simulate_swap_asset_a(to_swap)
                if leg.in_asset == leg.backend.asset_a
                else await leg.backend.simulate_swap_asset_b(to_swap)
            )

            if isinstance(leg.backend, NeutronAstroportPoolProvider):
                ctx.log_route(
                    route_ent,
                    "info",
                    "Submitting arb to contract: %s",
                    [leg.backend.contract_info.address],
                )

            if isinstance(leg.backend, OsmosisPoolProvider):
                ctx.log_route(
                    route_ent,
                    "info",
                    "Submitting arb to pool: %d",
                    [leg.backend.pool_id],
                )

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
            ctx.log_route(
                route_ent,
                "info",
                "Executed leg %s -> %s: %s",
                [
                    leg.in_asset(),
                    leg.out_asset(),
                    tx.tx_hash,
                ],
            )

        next(
            (leg_repr for leg_repr in route_ent.route if str(leg_repr) == str(leg))
        ).executed = True

        prev_leg = leg


async def rebalance_portfolio(
    pools: dict[str, dict[str, list[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
    ctx: Ctx[Any],
) -> Ctx[Any]:
    """
    Sells any denoms on any chains we have endpoints for
    that have a value in the base denom above the rebalancing threshold.
    """

    gas_denoms: set[str] = {
        pool.chain_fee_denom
        for pool_base in pools.values()
        for pool_set in pool_base.values()
        for pool in pool_set
    }

    # Sell qualifying denoms across all chains
    for chain_id in ctx.endpoints.keys():
        logger.info("Rebalancing portfolio for chain %s", chain_id)

        chain_meta = await chain_info(chain_id, ctx.http_session)

        if not chain_meta:
            continue

        # Find paths to sell all denoms
        # If the resulting amount of untrn is greater
        # than the threshold, execute the arb
        denom_coins = try_multiple_clients(
            ctx.clients[chain_id],
            lambda client: client.query_bank_all_balances(
                Address(
                    ctx.wallet.public_key(),
                    prefix=chain_meta.bech32_prefix,
                ),
            ),
        )

        if not denom_coins:
            continue

        # The base denom should not be rebalanced since that is what we are rebalancing to
        qualifying_denoms_balances = (
            (coin.denom, int(coin.amount))
            for coin in denom_coins
            if coin.denom != ctx.cli_args["base_denom"]
        )

        async def eval_sell_denom(denom: str, sell_denom: str, balance: int) -> None:
            """
            - Finds a route to sell the given denom
            - Calculates the execution plan
            - Executes the plan if the ending amount if greater than the
            base denom liquidation threshold
            """

            if denom in gas_denoms:
                return

            logger.info("Rebalancing %d %s", balance, denom)

            route_ent_route = await anext(
                listen_routes_with_depth_dfs(
                    ctx.cli_args["hops"],
                    denom,
                    sell_denom,
                    set(),
                    pools,
                    auctions,
                    ctx,
                )
            )

            if not route_ent_route:
                logger.info("No route to rebalance %s; skipping", denom)

                return

            route_ent, route = route_ent_route

            # For logging
            _, execution_plan = await quantities_for_route_profit(
                balance, route, route_ent, ctx, seek_profit=False
            )

            # The execution plan was aborted
            if len(execution_plan) <= len(route):
                ctx.log_route(
                    route_ent,
                    "info",
                    "Insufficient execution planning for rebalancing for %s; skipping",
                    [denom],
                )

                return

            # Check that the execution plan results in a liquidatable quantity
            if execution_plan[-1] < ctx.cli_args["rebalance_threshold"]:
                ctx.log_route(
                    route_ent,
                    "info",
                    "Not enough funds for rebalancing %s; skipping",
                    [denom],
                )

                return

            ctx.log_route(
                route_ent, "info", "Executing rebalancing plan for %s", [denom]
            )

            # Execute the plan
            route_ent.quantities = execution_plan
            ctx.update_route(route_ent)

            try:
                await exec_arb(route_ent, 0, execution_plan, route, ctx)
            except Exception:
                ctx.log_route(
                    route_ent,
                    "error",
                    "Arb failed - rebalancing of %s failed: %s",
                    [
                        denom,
                        traceback.format_exc().replace(
                            "\n",
                            f"\n{route_ent.uid}- Arb failed - failed to rebalance funds: ",
                        ),
                    ],
                )

        await asyncio.gather(
            *[
                eval_sell_denom(denom, ctx.cli_args["base_denom"], balance)
                for denom, balance in qualifying_denoms_balances
            ]
        )

    return ctx


async def listen_routes_with_depth_dfs(
    depth: int,
    src: str,
    dest: str,
    required_leg_types: set[str],
    pools: dict[str, dict[str, list[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
    ctx: Ctx[Any],
) -> AsyncGenerator[tuple[Route, list[Leg]], None]:
    denom_cache: dict[str, dict[str, list[str]]] = {}

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
            or path[-1].in_asset()
            in [
                denom
                for denom_list in denom_cache[path[-2].out_asset()].values()
                for denom in denom_list
            ]
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
        if len(path) > 1 and dest == prev_pool.out_asset():
            if len(required_leg_types - set((fmt_route_leg(leg) for leg in path))) > 0:
                return

            r: Route = ctx.queue_route(path, 0, 0, [])

            yield (r, path)

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
                    prev_pool.backend.chain_id, end, ctx.http_session, ctx.denom_map
                )

                denom_cache[end] = {
                    info[0].chain_id: [i.denom for i in info]
                    for info in (
                        denom_infos
                        + [
                            [
                                DenomChainInfo(
                                    denom=end,
                                    chain_id=prev_pool.backend.chain_id,
                                )
                            ]
                        ]
                    )
                    if len(info) > 0 and info[0].chain_id
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
                    for denom_set in denom_cache[end].values()
                    for denom in denom_set
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
                    for denom_set in denom_cache[end].values()
                    for denom in denom_set
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

    next_jobs = [next_legs([leg]) for leg in start_legs]

    if len(next_jobs) == 0:
        return

    routes = stream.merge(*next_jobs)

    async with routes.stream() as streamer:
        async for route in streamer:
            yield route


async def recover_funds(
    r: Route, curr_leg: Leg, route: list[Leg], ctx: Ctx[Any]
) -> None:
    """
    Returns back to USDC if a leg fails by backtracking.
    """

    ctx.log_route(
        r,
        "info",
        "Recovering funds in denom %s from current denom %s on chain %s",
        [
            ctx.cli_args["base_denom"],
            curr_leg.in_asset(),
            curr_leg.backend.chain_id,
        ],
    )

    route = route[: route.index(curr_leg) - 1]
    backtracked = list(
        reversed([Leg(leg.out_asset, leg.in_asset, leg.backend) for leg in route])
    )

    balance_resp = try_multiple_clients(
        ctx.clients[curr_leg.backend.chain_id],
        lambda client: client.query_bank_balance(
            Address(ctx.wallet.public_key(), prefix=curr_leg.backend.chain_prefix),
            curr_leg.in_asset(),
        ),
    )

    if not balance_resp:
        raise ValueError(f"Couldn't get balance for asset {curr_leg.in_asset()}.")

    if curr_leg.backend.chain_id != backtracked[0].backend.chain_id:
        to_transfer = min(balance_resp, r.quantities[-2])

        await transfer(
            r,
            curr_leg.in_asset(),
            curr_leg,
            backtracked[0],
            ctx,
            to_transfer,
        )

    resp = await quantities_for_route_profit(
        balance_resp, backtracked, r, ctx, seek_profit=False
    )

    if not resp:
        raise ValueError("Couldn't get execution plan.")

    profit, quantities = resp

    r = ctx.queue_route(
        backtracked, -r.theoretical_profit, -r.expected_profit, quantities
    )

    ctx.log_route(r, "info", "Executing recovery", [])

    await exec_arb(r, profit, quantities, backtracked, ctx)


async def transfer(
    route: Route,
    denom: str,
    prev_leg: Leg,
    leg: Leg,
    ctx: Ctx[Any],
    swap_balance: int,
) -> None:
    """
    Synchronously executes an IBC transfer from one leg in an arbitrage
    trade to the next, moving `swap_balance` of the asset_b in the source
    leg to asset_a in the destination leg. Returns true if the transfer
    succeeded.
    """

    denom_infos_on_dest = await denom_info_on_chain(
        prev_leg.backend.chain_id,
        denom,
        leg.backend.chain_id,
        ctx.http_session,
        ctx.denom_map,
    )

    if not denom_infos_on_dest:
        raise ValueError(
            f"Missing denom info for transfer {denom} ({prev_leg.backend.chain_id}) -> {leg.backend.chain_id}"
        )

    ibc_route = await denom_route(
        prev_leg.backend.chain_id,
        denom,
        leg.backend.chain_id,
        denom_infos_on_dest[0].denom,
        ctx.http_session,
    )

    if not ibc_route or len(ibc_route) == 0:
        raise ValueError(f"No route from {denom} to {leg.backend.chain_id}")

    src_channel_id = ibc_route[0].channel
    sender_addr = str(
        Address(ctx.wallet.public_key(), prefix=ibc_route[0].from_chain.bech32_prefix)
    )
    receiver_addr = str(
        Address(ctx.wallet.public_key(), prefix=ibc_route[0].to_chain.bech32_prefix)
    )

    memo: Optional[str] = None

    for ibc_leg in reversed(ibc_route[1:]):
        memo = json.dumps(
            {
                "forward": {
                    "receiver": "pfm",
                    "port": ibc_leg.port,
                    "channel": ibc_leg.channel,
                    "timeout": "10m",
                    "retries": 2,
                    "next": memo,
                }
            }
        )

    await transfer_raw(
        denom,
        ibc_route[0].from_chain.chain_id,
        prev_leg.backend.chain_fee_denom,
        src_channel_id,
        ibc_route[0].to_chain.chain_id,
        sender_addr,
        receiver_addr,
        ctx,
        swap_balance,
        memo=memo,
    )


async def transfer_raw(
    denom: str,
    src_chain_id: str,
    src_chain_fee_denom: str,
    src_channel_id: str,
    dest_chain_id: str,
    sender_addr: str,
    receiver_addr: str,
    ctx: Ctx[Any],
    swap_balance: int,
    memo: Optional[str] = None,
    route: Optional[Route] = None,
) -> None:
    """
    Synchronously executes an IBC transfer from one leg in an arbitrage
    trade to the next, moving `swap_balance` of the asset_b in the source
    leg to asset_a in the destination leg. Returns true if the transfer
    succeeded.
    """

    # Create a messate transfering the funds
    msg = (
        tx_pb2.MsgTransfer(  # pylint: disable=no-member
            source_port="transfer",
            source_channel=src_channel_id,
            sender=sender_addr,
            receiver=receiver_addr,
            timeout_timestamp=time.time_ns() + 600 * 10**9,
            memo=memo,
        )
        if memo
        else tx_pb2.MsgTransfer(  # pylint: disable=no-member
            source_port="transfer",
            source_channel=src_channel_id,
            sender=sender_addr,
            receiver=receiver_addr,
            timeout_timestamp=time.time_ns() + 600 * 10**9,
        )
    )

    msg.token.CopyFrom(
        coin_pb2.Coin(  # pylint: disable=maybe-no-member
            denom=denom, amount=str(swap_balance)
        )
    )

    acc = try_multiple_clients_fatal(
        ctx.clients[src_chain_id],
        lambda client: client.query_account(str(sender_addr)),
    )

    tx = Transaction()
    tx.add_message(msg)
    tx.seal(
        SigningCfg.direct(ctx.wallet.public_key(), acc.sequence),
        f"100000{src_chain_fee_denom}",
        1000000,
    )
    tx.sign(ctx.wallet.signer(), src_chain_id, acc.number)
    tx.complete()

    submitted = try_multiple_clients_fatal(
        ctx.clients[src_chain_id],
        lambda client: client.broadcast_tx(tx),
    ).wait_to_complete()

    if route:
        ctx.log_route(
            route,
            "info",
            "Submitted IBC transfer from src %s to %s: %s",
            [
                src_chain_id,
                dest_chain_id,
                submitted.tx_hash,
            ],
        )

    # Continuously check for a package acknowledgement
    # or cancel the arb if the timeout passes
    # Future note: This could be async so other arbs can make
    # progress while this is happening
    async def transfer_or_continue() -> bool:
        if route:
            ctx.log_route(
                route, "info", "Checking IBC transfer status %s", [submitted.tx_hash]
            )

        # Check for a package acknowledgement by querying osmosis
        ack_resp = await try_multiple_rest_endpoints(
            ctx.endpoints[src_chain_id]["http"],
            (
                f"/ibc/core/channel/v1/channels/{src_channel_id}/"
                f"ports/transfer/packet_acks/"
                f"{submitted.response.events['send_packet']['packet_sequence']}"
            ),
            ctx.http_session,
        )

        # try again
        if not ack_resp:
            if route:
                ctx.log_route(
                    route,
                    "info",
                    "IBC transfer %s has not yet completed; waiting...",
                    [submitted.tx_hash],
                )

            return False

        # Stop trying, since the transfer succeeded
        return True

    timeout = time.time() + IBC_TRANSFER_TIMEOUT_SEC

    while time.time() < timeout:
        time.sleep(IBC_TRANSFER_POLL_INTERVAL_SEC)

        if await transfer_or_continue():
            return

    raise ValueError("IBC transfer timed out.")


async def quantities_for_route_profit(
    starting_amount: int,
    route: list[Leg],
    r: Route,
    ctx: Ctx[Any],
    seek_profit: Optional[bool] = True,
) -> tuple[int, list[int]]:
    """
    Calculates what quantities should be used to obtain
    a net profit of `profit` by traversing the route.
    """

    if len(route) <= 1:
        return (0, [])

    quantities: list[int] = [starting_amount]

    while (seek_profit and quantities[-1] - quantities[0] <= 0) or len(
        quantities
    ) <= len(route):
        ctx.log_route(r, "info", "Route has possible execution plan: %s", [quantities])

        if starting_amount < DENOM_QUANTITY_ABORT_ARB:
            logger.debug(
                "Hit investment backstop (%d) in route planning: %s (%s)",
                DENOM_QUANTITY_ABORT_ARB,
                starting_amount,
                quantities,
            )

            return (0, [])

        quantities = [starting_amount]

        for leg in route:
            if quantities[-1] == 0:
                quantities = [starting_amount]

                break

            prev_amt = quantities[-1]

            if isinstance(leg.backend, AuctionProvider):
                if leg.in_asset != leg.backend.asset_a:
                    return (0, [])

                if await leg.backend.remaining_asset_b() == 0:
                    return (0, [])

                quantities.append(
                    min(
                        int(await leg.backend.exchange_rate() * prev_amt),
                        await leg.backend.remaining_asset_b(),
                    )
                )

                continue

            if leg.in_asset == leg.backend.asset_a:
                quantities.append(
                    int(await leg.backend.simulate_swap_asset_a(prev_amt))
                )

                pool_liquidity = await leg.backend.balance_asset_b()

                if (
                    pool_liquidity == 0
                    or Decimal(quantities[-1]) / Decimal(pool_liquidity)
                    > MAX_POOL_LIQUIDITY_TRADE
                ):
                    break

                continue

            quantities.append(int(await leg.backend.simulate_swap_asset_b(prev_amt)))

            pool_liquidity = await leg.backend.balance_asset_a()

            if (
                pool_liquidity == 0
                or Decimal(quantities[-1]) / Decimal(pool_liquidity)
                > MAX_POOL_LIQUIDITY_TRADE
            ):
                break

        starting_amount = int(Decimal(starting_amount) / Decimal(2.0))

    ctx.log_route(r, "info", "Got execution plan: %s", [quantities])

    if quantities[-1] - quantities[0] > 0:
        ctx.log_route(r, "info", "Route is profitable: %s", [fmt_route(route)])

    return (quantities[-1] - quantities[0], quantities)


async def route_base_denom_profit(
    starting_amount: int,
    route: list[Leg],
) -> int:
    """
    Determines whether or not a route is profitable
    base on the exchange rates present in the route.
    """

    exchange_rates: list[Decimal] = []

    for leg in route:
        if isinstance(leg.backend, AuctionProvider):
            if leg.in_asset != leg.backend.asset_a:
                return 0

            if await leg.backend.remaining_asset_b() == 0:
                return 0

            exchange_rates.append(await leg.backend.exchange_rate())

            continue

        balance_asset_a, balance_asset_b = (
            await leg.backend.balance_asset_a(),
            await leg.backend.balance_asset_b(),
        )

        if balance_asset_a == 0 or balance_asset_b == 0:
            return 0

        if leg.in_asset == leg.backend.asset_a:
            exchange_rates.append(Decimal(balance_asset_b) / Decimal(balance_asset_a))

            continue

        exchange_rates.append(Decimal(balance_asset_a) / Decimal(balance_asset_b))

    return int(reduce(operator.mul, exchange_rates, starting_amount)) - starting_amount


def pools_to_ser(
    pools: dict[str, dict[str, list[PoolProvider]]]
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """
    Serializes all pools in `pools`.
    """

    return {
        k: {k1: [pool.dump() for pool in v1] for (k1, v1) in v.items()}
        for (k, v) in pools.items()
    }


def auctions_to_ser(
    auctions: dict[str, dict[str, AuctionProvider]]
) -> dict[str, dict[str, dict[str, Any]]]:
    """
    Serializes all auctions in `auctions`.
    """

    return {k: {k1: v1.dump() for (k1, v1) in v.items()} for (k, v) in auctions.items()}
