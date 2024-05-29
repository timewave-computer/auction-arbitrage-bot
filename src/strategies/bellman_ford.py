"""
Implements an arbitrage strategy based on bellman ford.
"""

from queue import Queue
import json
from functools import reduce
import logging
import itertools
import math
from decimal import Decimal
import asyncio
import multiprocessing
from datetime import datetime
import threading
from dataclasses import dataclass
from typing import Union, Iterator, Optional, Any, Self
from src.contracts.pool.provider import PoolProvider
from src.contracts.pool.astroport import NeutronAstroportPoolProvider
from src.contracts.pool.osmosis import OsmosisPoolProvider
from src.contracts.auction import AuctionProvider
from src.contracts.pool.provider import PoolProvider
from src.contracts.leg import Leg
from src.scheduler import Ctx
from src.strategies.util import fmt_route, exec_arb, route_base_denom_profit
from src.util import (
    DISCOVERY_CONCURRENCY_FACTOR,
    denom_info,
    int_to_decimal,
    try_multiple_clients,
    DenomChainInfo,
)
from cosmpy.crypto.address import Address  # type: ignore


logger = logging.getLogger(__name__)


@dataclass
class Edge:
    """
    Represents a connection from one asset to another with a
    particular multiplying effect.
    """

    backend: Leg
    weight: Decimal


@dataclass
class State:
    """
    A strategy state for a bellman ford strategy that provides
    caching of global up-to-date pricing information.
    """

    # A mapping from a given denom to each corresponding denom
    # and all auctions/pools that provide the pairing,
    # with their weight
    denom_cache: dict[str, dict[str, str]]


async def pair_provider_edge(
    src: str, pair: str, provider: Union[PoolProvider, AuctionProvider]
) -> tuple[str, Optional[Edge]]:
    """
    Calculates edge weights for all pairs connected to a base pair.
    """

    logger.debug("Getting weight for edge %s -> %s", src, pair)

    if isinstance(provider, AuctionProvider):
        if await provider.remaining_asset_b() == 0:
            return (pair, None)

        return (
            pair,
            Edge(
                Leg(
                    (
                        provider.asset_a
                        if provider.asset_a() == pair
                        else provider.asset_b
                    ),
                    (
                        provider.asset_b
                        if provider.asset_a() == pair
                        else provider.asset_a
                    ),
                    provider,
                ),
                -Decimal.ln(int_to_decimal(await provider.exchange_rate())),
            ),
        )

    balance_asset_a, balance_asset_b = (
        await provider.balance_asset_a(),
        await provider.balance_asset_b(),
    )

    if balance_asset_a == 0 or balance_asset_b == 0:
        return (pair, None)

    if provider.asset_a() == src:
        return (
            pair,
            Edge(
                Leg(
                    (
                        provider.asset_a
                        if provider.asset_a() == pair
                        else provider.asset_b
                    ),
                    (
                        provider.asset_b
                        if provider.asset_a() == pair
                        else provider.asset_a
                    ),
                    provider,
                ),
                -Decimal.ln(Decimal(balance_asset_b) / Decimal(balance_asset_a)),
            ),
        )

    return (
        pair,
        Edge(
            Leg(
                (provider.asset_a if provider.asset_a() == pair else provider.asset_b),
                (provider.asset_b if provider.asset_a() == pair else provider.asset_a),
                provider,
            ),
            -Decimal.ln(Decimal(balance_asset_a) / Decimal(balance_asset_b)),
        ),
    )


async def weights_for(
    src: str,
    pools: dict[str, dict[str, list[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
    ctx: Ctx,
    n_denoms: Queue[int],
) -> tuple[str, dict[str, list[Edge]]]:
    """
    Gets all denoms which have a direct conncetion to the src denom,
    including cross-chain connections.
    """

    logger.debug("Fetching weights for src %s", src)

    # Also include providers for all denoms that this src is associated with
    if src not in ctx.state.denom_cache:
        denom_infos = await denom_info(
            "neutron-1",
            src,
            ctx.http_session,
        )

        ctx.state.denom_cache[src] = {
            info.chain_id: info.denom
            for info in (
                denom_infos
                + [
                    DenomChainInfo(
                        denom=src,
                        port=None,
                        channel=None,
                        chain_id="neutron-1",
                    )
                ]
            )
            if info.chain_id
        }

    matching_denoms: dict[str, str] = ctx.state.denom_cache.get(src, {})

    providers_for_pair: list[tuple[str, str, Union[PoolProvider, AuctionProvider]]] = [
        *(
            (src, pair, pool)
            for (pair, pool_set) in pools.get(src, {}).items()
            for pool in pool_set
        ),
        *((src, pair, auction) for (pair, auction) in auctions.get(src, {}).items()),
        *(
            (src, pair, pool)
            for denom in matching_denoms.values()
            for (pair, pool_set) in pools.get(denom, {}).items()
            for pool in pool_set
        ),
        *(
            (src, pair, auction)
            for denom in matching_denoms.values()
            for (pair, auction) in auctions.get(denom, {}).items()
        ),
    ]

    logger.debug("Getting edge weights for %d peer pools", len(providers_for_pair))

    with_weights: list[tuple[str, Optional[Edge]]] = await asyncio.gather(
        *(
            pair_provider_edge(src, pair, provider)
            for (src, pair, provider) in providers_for_pair
        )
    )

    pair_edges: dict[str, list[Edge]] = {}

    for pair, edge in with_weights:
        if edge is None:
            continue

        pair_edges[pair] = pair_edges.get(pair, []) + [edge]

    logger.debug("Got weights for denom #%d", n_denoms.get())

    return (src, pair_edges)


async def strategy(
    ctx: Ctx,
    pools: dict[str, dict[str, list[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
) -> Ctx:
    """
    Finds new arbitrage opportunities using the context, pools, and auctions.
    """

    if not ctx.state:
        ctx.state = State({})

    if ctx.cli_args["cmd"] == "dump":
        return ctx.cancel()

    # Report route stats to user
    logger.info(
        "Finding profitable routes",
    )

    route = await route_bellman_ford(
        ctx.cli_args["base_denom"],
        pools,
        auctions,
        ctx.cli_args["require_leg_types"],
        ctx,
    )

    if not route:
        return ctx

    logger.debug("Route queued: %s", fmt_route(route))

    balance_resp = try_multiple_clients(
        ctx.clients["neutron"],
        lambda client: client.query_bank_balance(
            Address(ctx.wallet.public_key(), prefix="neutron"),
            ctx.cli_args["base_denom"],
        ),
    )

    if not balance_resp:
        return ctx

    profit = route_base_denom_profit(
        balance_resp,
        route,
    )

    if profit < ctx.cli_args["profit_margin"]:
        logger.debug(
            "Route is not profitable with profit of %d: %s",
            profit,
            fmt_route(route),
        )

        return ctx

    logger.info("Executing route with profit of %d: %s", profit, fmt_route(route))

    try:
        await exec_arb(route, ctx)
    except Exception as e:
        logger.error("Arb failed %s: %s", fmt_route(route), e)

    logger.info("Completed arbitrage round")

    return ctx


async def route_bellman_ford(
    src: str,
    pools: dict[str, dict[str, list[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
    required_leg_types: set[str],
    ctx: Ctx,
) -> Optional[list[Leg]]:
    """
    Searches for profitable arbitrage routes via the recorded weights.
    """

    if not ctx.state:
        return None

    denoms = {*pools.keys(), *auctions.keys()}

    n_denoms_fetch: Queue[int] = Queue()

    for i in range(len(denoms)):
        n_denoms_fetch.put(i)

    tup_denom_pair_edges = await asyncio.gather(
        *[weights_for(denom, pools, auctions, ctx, n_denoms_fetch) for denom in denoms]
    )
    denom_pair_edges: dict[str, dict[str, list[Edge]]] = {
        denom: pair_edges for (denom, pair_edges) in tup_denom_pair_edges
    }

    dist: dict[str, tuple[Decimal, Optional[Edge]]] = {
        denom: (Decimal("inf"), None) for denom in denoms
    }
    pred: dict[str, Optional[tuple[str, Edge]]] = {denom: None for denom in denoms}

    dist[src] = (Decimal(0), None)

    for denom in denoms:
        logger.debug("Relaxing edges for %s", denom)

        pair_edges = denom_pair_edges[denom]

        for pair, edges in pair_edges.items():
            for edge in edges:
                if dist[denom][0] + edge.weight >= dist[pair][0]:
                    continue

                dist[pair] = (dist[denom][0] + edge.weight, edge)
                pred[pair] = (denom, edge)

    for denom, pair_edges in denom_pair_edges.items():
        for pair, edges in pair_edges.items():
            for edge in edges:
                if dist[denom][0] + edge.weight >= dist[pair][0]:
                    continue

                pred[pair] = (denom, edge)

                visited = set()
                visited.add(pair)

                while not denom in visited:
                    visited.add(denom)

                    pred_denom = pred[denom]

                    if not pred_denom:
                        continue

                    denom = pred_denom[0]

                pred_denom = pred[denom]

                if not pred_denom:
                    continue

                path: list[Leg] = [pred_denom[1].backend]
                pair = pred_denom[0]

                while pair != denom:
                    pred_pair = pred[pair]

                    if not pred_pair:
                        break

                    path = [pred_pair[1].backend] + path
                    pair = pred_pair[0]

                return path

    return None
