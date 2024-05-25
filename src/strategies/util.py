"""
Defines common utilities shared across arbitrage strategies.
"""


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
