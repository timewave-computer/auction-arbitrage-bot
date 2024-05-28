"""
Defines common utilities shared across arbitrage strategies.
"""

from math import floor
import operator
from functools import reduce
from decimal import Decimal
import logging
import time
from typing import Optional
from src.contracts.leg import Leg
from src.contracts.auction import AuctionProvider
from src.contracts.pool.provider import PoolProvider
from src.contracts.pool.osmosis import OsmosisPoolProvider
from src.contracts.pool.astroport import (
    NeutronAstroportPoolProvider,
)

from src.util import (
    int_to_decimal,
    decimal_to_int,
    IBC_TRANSFER_TIMEOUT_SEC,
    IBC_TRANSFER_POLL_INTERVAL_SEC,
    try_multiple_rest_endpoints,
    try_multiple_clients_fatal,
    try_multiple_clients,
    DenomChainInfo,
    denom_info_on_chain,
    MAX_TRADE_IN_POOL_FRACTION,
)
from src.scheduler import Ctx
import urllib3
import aiohttp
import asyncio
import grpc
from cosmos.base.v1beta1 import coin_pb2
from cosmpy.crypto.address import Address  # type: ignore
from cosmpy.aerial.tx import Transaction, SigningCfg  # type: ignore
from cosmpy.aerial.tx_helpers import SubmittedTx  # type: ignore
from ibc.applications.transfer.v1 import tx_pb2

logger = logging.getLogger(__name__)


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
    route: list[Leg],
    ctx: Ctx,
) -> None:
    """
    Executes a list of arbitrage trades composed of multiple hops.

    Takes a list of arbitrage trades, and a list of prices for each hop
    in each trade.
    """

    swap_balance = ctx.state.balance

    if not swap_balance:
        raise ValueError("Couldn't fetch wallet balance")

    profit, quantities = quantities_for_route_profit(
        swap_balance,
        ctx.cli_args["profit_margin"],
        route,
    )

    if len(quantities) < len(route):
        raise ValueError(
            f"Insufficient execution planning for route {fmt_route(route)}; canceling"
        )

    if profit < ctx.cli_args["profit_margin"]:
        raise ValueError(
            f"Insufficient realized profit for route {fmt_route(route)}; canceling"
        )

    prev_leg: Optional[Leg] = None

    # Submit arb trades for all profitable routes
    logger.info(
        ("Queueing candidpate arbitrage opportunity with " "route with %d hop(s): %s"),
        len(route),
        fmt_route(route),
    )

    for leg, to_swap in zip(route, quantities):
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
            to_swap
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


def quantities_for_route_profit(
    starting_amount: int,
    target_profit: int,
    route: list[Leg],
) -> tuple[int, list[int]]:
    """
    Calculates what quantities should be used to obtain
    a net profit of `profit` by traversing the route.
    """

    if len(route) == 0:
        return (0, [])

    starting_amount = min(
        starting_amount * MAX_TRADE_IN_POOL_FRACTION, route[0].backend.balance_asset_a()
    )

    quantities: list[int] = [starting_amount]

    while quantities[-1] - quantities[0] < target_profit:
        if starting_amount < target_profit:
            logger.debug(
                "Hit investment backstop (%d) in route planning: %s",
                target_profit,
                starting_amount,
            )

            return (0, [])

        quantities = [starting_amount]

        for leg in route:
            if quantities[-1] == 0:
                break

            prev_amt = quantities[-1]

            if isinstance(leg.backend, AuctionProvider):
                if leg.in_asset != leg.backend.asset_a:
                    return (0, [])

                if leg.backend.remaining_asset_b() == 0:
                    return (0, [])

                quantities.append(
                    int(int_to_decimal(leg.backend.exchange_rate()) * prev_amt)
                )

                continue

            if leg.in_asset == leg.backend.asset_a:
                quantities.append(int(leg.backend.simulate_swap_asset_a(prev_amt)))

                continue

            quantities.append(int(leg.backend.simulate_swap_asset_b(prev_amt)))

        starting_amount = int(Decimal(starting_amount) / Decimal(2.0))

    return (quantities[-1] - starting_amount, quantities)


def route_base_denom_profit(
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

            if leg.backend.remaining_asset_b() == 0:
                return 0

            exchange_rates.append(int_to_decimal(leg.backend.exchange_rate()))

            continue

        balance_asset_a, balance_asset_b = (
            leg.backend.balance_asset_a(),
            leg.backend.balance_asset_b(),
        )

        if balance_asset_a == 0 or balance_asset_b == 0:
            return 0

        if leg.in_asset == leg.backend.asset_a:
            exchange_rates.append(Decimal(balance_asset_b) / Decimal(balance_asset_a))

            continue

        exchange_rates.append(Decimal(balance_asset_a) / Decimal(balance_asset_b))

    return starting_amount - reduce(operator.mul, exchange_rates, starting_amount)
