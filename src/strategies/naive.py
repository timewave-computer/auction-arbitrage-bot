"""
Implements an arbitrage strategy with an arbitrary number
of hops using all available providers.
"""

import random
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
            self.routes: List[List[Union[PoolProvider, AuctionProvider]]] = ()

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

    if ctx.cli_args["cmd"] == "dump":
        return ctx.cancel()

    workers = []
    to_exec: Queue[List[Union[PoolProvider, AuctionProvider]]] = Queue(maxsize=0)

    # Report route stats to user
    logger.info(
        "Finding profitable routes",
    )

    workers.append(
        threading.Thread(
            target=listen_routes_with_depth_dfs,
            args=[
                to_exec,
                ctx.cli_args["hops"],
                ctx.cli_args["base_denom"],
                set(ctx.cli_args["require_leg_types"]),
                pools,
                auctions,
            ],
        )
    )
    workers[-1].start()

    while True:
        route = to_exec.get()

        logger.info("Route queued: %s", fmt_route(route))

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
            ctx.cli_args["base_denom"],
            balance_resp,
            route,
        )

        if profit < ctx.cli_args["profit_margin"]:
            logger.info(
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

    return leg.kind


def fmt_route(route: list[Union[PoolProvider, AuctionProvider]]) -> str:
    return " -> ".join(
        map(
            lambda route_leg: fmt_route_leg(route_leg)
            + ": "
            + route_leg.asset_a()
            + " - "
            + route_leg.asset_b(),
            route,
        )
    )


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
        fmt_route(route),
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
            logger.info(
                "Transfering %s %s from %s -> %s",
                to_swap,
                prev_asset,
                prev_leg.chain_id,
                leg.chain_id,
            )

            # Ensure that there is at least 5k of the base chain denom
            # at all times
            if prev_asset == prev_leg.chain_fee_denom:
                to_swap -= 5000

            # Cancel arb if the transfer fails
            try:
                transfer(prev_asset, prev_leg, leg, ctx, to_swap)
            except Exception as e:
                logger.error(
                    "Failed to transfer funds from %s -> %s: %s",
                    prev_leg.chain_id,
                    leg.chain_id,
                    e,
                )

                return

            logger.info("Transfer succeeded: %s -> %s", prev_leg.chain_id, leg.chain_id)

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
        else:
            logger.info("Arb leg can be executed atomically; no transfer necessary")

        # Ensure that there is at least 5k of the base chain denom
        # at all times
        if assets[0]() == leg.chain_fee_denom:
            to_swap -= 5000

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
):
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
        raise ValueError("Missing denom info for target chain in IBC transfer")

    channel_info = try_multiple_rest_endpoints(
        ctx.endpoints[leg.chain_id.split("-")[0]]["http"],
        f"/ibc/core/channel/v1/channels/{denom_info.channel}/ports/{denom_info.port}",
    )

    if not channel_info:
        raise ValueError("Missing channel info for target chain in IBC transfer")

    # Not enough info to complete the transfer
    if not denom_info or not denom_info.port or not denom_info.channel:
        raise ValueError("Missing channel info for target chain in IBC transfer")

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
            denom=denom, amount=str(swap_balance)
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
    ).wait_to_complete()

    logger.info(
        "Submitted IBC transfer from src %s to %s: %s",
        prev_leg.chain_id,
        leg.chain_id,
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
            leg.endpoints,
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
    base_denom: str,
    starting_amount: int,
    route: List[Union[PoolProvider, AuctionProvider]],
) -> tuple[int, list[int]]:
    """
    Calculates the profit that can be obtained by following the route.
    """

    prev_asset = base_denom
    prev_leg: Optional[Union[PoolProvider, AuctionProvider]] = None
    quantity_received = starting_amount
    quantities: list[int] = []

    for leg in route:
        norm_prev_asset = (
            prev_asset
            if not prev_leg or prev_leg.chain_id == leg.chain_id
            else denom_info_on_chain(prev_leg.chain_id, prev_asset, leg.chain_id)
        )

        assets = (
            [leg.asset_a, leg.asset_b]
            if norm_prev_asset == leg.asset_a()
            else [leg.asset_b, leg.asset_a]
        )

        if quantity_received == 0:
            break

        if isinstance(leg, PoolProvider):
            if norm_prev_asset == leg.asset_a():
                quantities.append(int(leg.simulate_swap_asset_a(quantity_received)))
                quantity_received = quantities[-1]
            else:
                quantities.append(int(leg.simulate_swap_asset_b(quantity_received)))
                quantity_received = quantities[-1]
        elif norm_prev_asset == leg.asset_a():
            quantities.append(leg.exchange_rate() * quantity_received)
            quantity_received = quantities[-1]

        prev_asset = assets[1]()
        prev_leg = leg

    return (quantity_received - starting_amount, quantities)


def listen_routes_with_depth_dfs(
    routes: Queue[List[Union[PoolProvider, AuctionProvider]]],
    depth: int,
    src: str,
    required_leg_types: set[str],
    pools: dict[str, dict[str, List[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
) -> List[List[Union[PoolProvider, AuctionProvider]]]:
    denom_cache: dict[str, dict[str, str]] = {}

    def next_legs(path: list[Union[PoolProvider, AuctionProvider]]) -> None:
        nonlocal denom_cache
        nonlocal routes

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
        if len(path) > 1 and src in {path[-1].asset_a(), path[-1].asset_b()}:
            if (
                len(required_leg_types - set((fmt_route_leg(leg) for leg in path))) > 0
                or len(path) < depth
            ):
                return

            logger.info(
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
        end: Optional[str] = None

        if len(path) == 1:
            end = (
                prev_pool.asset_a()
                if prev_pool.asset_a() != src
                else prev_pool.asset_b()
            )

            denom_cache[end] = {
                info.chain_id: info.denom
                for info in denom_info(prev_pool.chain_id, end)
                + [
                    DenomChainInfo(
                        denom=end,
                        port=None,
                        channel=None,
                        chain_id=prev_pool.chain_id,
                    )
                ]
            }
        else:
            prev_prev_pool = path[-2]

            # There are two previous pools. The end
            # of the most recent one is the denom
            # not shared between the two on the same chain
            # Use the prev's chain as a common denom
            denoms_prev = [prev_pool.asset_a(), prev_pool.asset_b()]

            assert (
                prev_prev_pool.asset_a() in denom_cache
                or prev_prev_pool.asset_b() in denom_cache
            )
            assert prev_pool.chain_id in denom_cache.get(
                prev_prev_pool.asset_a(), {}
            ) or prev_pool.chain_id in denom_cache.get(prev_prev_pool.asset_b(), {})

            denoms_prev_prev = [
                denom_cache.get(prev_prev_pool.asset_a(), {}).get(
                    prev_pool.chain_id, None
                ),
                denom_cache.get(prev_prev_pool.asset_b(), {}).get(
                    prev_pool.chain_id, None
                ),
            ]

            assert not all((denom == None for denom in denoms_prev_prev))

            # Only ONE denom in prev can be shared by prev-prev
            # If more than one is shared, then we are running in circles, so we must stop here
            if (
                not (
                    denoms_prev[0] in denoms_prev_prev
                    or denoms_prev[1] in denoms_prev_prev
                )
            ) or (
                denoms_prev[0] in denoms_prev_prev
                and denoms_prev[1] in denoms_prev_prev
            ):
                return

            # And that denom is the end
            end = (
                prev_pool.asset_a()
                if denoms_prev[0] not in denoms_prev_prev
                else prev_pool.asset_b()
            )

            if not prev_pool.asset_a() in denom_cache:
                denom_cache[prev_pool.asset_a()] = {
                    info.chain_id: info.denom
                    for info in denom_info(prev_pool.chain_id, prev_pool.asset_a())
                    + [
                        DenomChainInfo(
                            denom=prev_pool.asset_a(),
                            port=None,
                            channel=None,
                            chain_id=prev_pool.chain_id,
                        )
                    ]
                }

            if not prev_pool.asset_b() in denom_cache:
                denom_cache[prev_pool.asset_b()] = {
                    info.chain_id: info.denom
                    for info in denom_info(prev_pool.chain_id, prev_pool.asset_b())
                    + [
                        DenomChainInfo(
                            denom=prev_pool.asset_b(),
                            port=None,
                            channel=None,
                            chain_id=prev_pool.chain_id,
                        )
                    ]
                }

            assert end == prev_pool.asset_a() or end == prev_pool.asset_b()

        assert end is not None

        # A pool is a candidate to be a next pool if it has a denom
        # contained in denom_cache[end] or one of its denoms *is* end
        next_pools = [
            # Atomic pools
            *auctions.get(end, {}).values(),
            *(pool for pool_set in pools.get(end, {}).values() for pool in pool_set),
            # IBC pools
            *(
                auction
                for denom in denom_cache[end].values()
                for auction in auctions.get(denom, {}).values()
            ),
            *(
                pool
                for denom in denom_cache[end].values()
                for pool_set in pools.get(denom, {}).values()
                for pool in pool_set
            ),
        ]
        random.shuffle(next_pools)

        for pool in next_pools:
            if (
                pool in path
                or len(
                    {pool.asset_a(), pool.asset_b()}
                    ^ {prev_pool.asset_a(), prev_pool.asset_b()}
                )
                <= 0
            ):
                continue

            next_legs(path + [pool])

    start_pools = [
        *auctions.get(src, {}).values(),
        *(pool for pool_set in pools.get(src, {}).values() for pool in pool_set),
    ]
    random.shuffle(start_pools)

    for pool in start_pools:
        next_legs([pool])
