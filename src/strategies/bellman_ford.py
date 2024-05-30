"""
Implements an arbitrage strategy based on bellman ford.
"""

from collections import deque
import random
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
from src.strategies.util import (
    fmt_route,
    exec_arb,
    route_base_denom_profit,
    quantities_for_route_profit,
)
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

    # Edge weights for providers representing a connection
    # between a base denom and a pair denom
    weights_cache: dict[str, dict[str, list[Edge]]]

    def poll(
        self,
        ctx: Ctx,
        pools: dict[str, dict[str, list[PoolProvider]]],
        auctions: dict[str, dict[str, AuctionProvider]],
    ) -> Self:
        """
        Polls the state for a potential update, leaving the state
        alone, or producing a new state.
        """

        self.weights_cache = {}

        return self


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
) -> tuple[str, dict[str, list[Edge]]]:
    """
    Gets all denoms which have a direct conncetion to the src denom,
    including cross-chain connections.
    """

    if not ctx.state:
        return (src, {})

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
        ctx.state = State({}, {})

    ctx = ctx.with_state(ctx.state.poll(ctx, pools, auctions))

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

    profit = await route_base_denom_profit(
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

    profit, quantities = await quantities_for_route_profit(
        ctx.state.balance,
        ctx.cli_args["profit_margin"],
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
        await exec_arb(profit, quantities, route, ctx)
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
    Searches for profitable arbitrage routes by finding negative cycles in the graph.
    """

    if not ctx.state:
        return None

    vertices: set[str] = {*pools.keys(), *auctions.keys()}

    if ctx.cli_args["pools"]:
        vertices = set(random.sample(sorted(vertices), ctx.cli_args["pools"] - 1))

    vertices = {*vertices, ctx.cli_args["base_denom"]}

    # How far a given denom is from the `src` denom
    distances: dict[str, Decimal] = {vertex: Decimal("inf") for vertex in vertices}
    distances[src] = Decimal(0)
    pred: dict[str, Leg] = {}

    # Number of times a vertex has been visited
    visits: dict[str, int] = {vertex: 0 for vertex in vertices}

    to_explore: deque[str] = deque()
    to_explore.append(src)

    while len(to_explore) != 0:
        pair = to_explore.popleft()

        def providers_for(a: str, b: str) -> Iterator[Leg]:
            providers = (
                x
                for x in (
                    *pools.get(a, {}).get(b, []),
                    *[auctions.get(a, {}).get(b, None)],
                )
                if x
            )

            return (
                Leg(
                    prov.asset_a if prov.asset_a() == a else prov.asset_b,
                    prov.asset_b if prov.asset_a() == a else prov.asset_a,
                    prov,
                )
                for prov in providers
            )

        if not src in ctx.state.weights_cache:
            pair_edges_for: tuple[str, dict[str, list[Edge]]] = await weights_for(
                src, pools, auctions, ctx
            )
            ctx.state.weights_cache[src] = pair_edges_for[1]

        edges_for = ctx.state.weights_cache.get(src, {})

        if not edges_for:
            continue

        for match_pair, edges in edges_for.items():
            # Get all pools that convert between these two assets
            for provider in providers_for(pair, match_pair):
                pair_edge: tuple[str, Optional[Edge]] = await pair_provider_edge(
                    src, pair, provider.backend
                )
                _, edge = pair_edge

                if not edge:
                    continue

                if distances[pair] + edge.weight < distances[match_pair]:
                    distances[match_pair] = distances[pair] + edge.weight
                    pred[match_pair] = edge.backend
                    visits[match_pair] = visits[pair] + 1

                    # Find the negative cycle
                    if visits[match_pair] == len(vertices):
                        curr: Leg = pred[src]
                        path: list[Leg] = [curr]

                        while curr.in_asset() != src:
                            path.append(pred[curr.in_asset()])
                            curr = pred[curr.in_asset()]

                        return path

                    if pair not in to_explore:
                        to_explore.appendleft(pair)

    return None
