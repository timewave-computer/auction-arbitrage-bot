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

    # Which chain a given denom exists on
    chain_cache: dict[str, str]

    # Edge weights for providers representing a connection
    # between a base denom and a pair denom
    weights: dict[Union[AuctionProvider, PoolProvider], tuple[Edge, Edge]]

    async def poll(
        self,
        ctx: Ctx,
        pools: dict[str, dict[str, list[PoolProvider]]],
        auctions: dict[str, dict[str, AuctionProvider]],
    ) -> Self:
        """
        Polls the state for a potential update, leaving the state
        alone, or producing a new state.
        """

        self.weights = {}

        vertices: set[Union[AuctionProvider, PoolProvider]] = {
            *(
                provider
                for base, pair_providers in pools.items()
                for providers in pair_providers.values()
                for provider in providers
            ),
            *(
                auction
                for base, pair_auctions in auctions.items()
                for auction in pair_auctions.values()
            ),
        }

        logger.info(
            "Building route tree with %d vertices (this may take a while)",
            len(vertices),
        )

        async def all_edges_for(
            vertex: Union[AuctionProvider, PoolProvider]
        ) -> tuple[
            Union[AuctionProvider, PoolProvider], Optional[Edge], Optional[Edge]
        ]:
            return (
                vertex,
                (await pair_provider_edge(vertex.asset_a(), vertex.asset_b(), vertex))[
                    1
                ],
                (await pair_provider_edge(vertex.asset_b(), vertex.asset_a(), vertex))[
                    1
                ],
            )

        # Calculate all ege weights
        weights: Iterator[tuple[Union[AuctionProvider, PoolProvider], Edge, Edge]] = (
            (prov, edge_a, edge_b)
            for prov, edge_a, edge_b in (
                await asyncio.gather(*[all_edges_for(vertex) for vertex in vertices])
            )
            if edge_a and edge_b
        )

        self.weights = {prov: (edge_a, edge_b) for prov, edge_a, edge_b in weights}

        logger.info("Got %d weights", len(weights.values()))

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


async def strategy(
    ctx: Ctx,
    pools: dict[str, dict[str, list[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
) -> Ctx:
    """
    Finds new arbitrage opportunities using the context, pools, and auctions.
    """

    if not ctx.state:
        ctx.state = State({}, {}, {})

    ctx = ctx.with_state(await ctx.state.poll(ctx, pools, auctions))

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

        async def providers_for(a: str) -> Iterator[Leg]:
            if not a in ctx.state.denom_cache:
                denom_infos = await denom_info(
                    (
                        "neutron-1"
                        if not a in ctx.state.chain_cache
                        else ctx.state.chain_cache[a]
                    ),
                    src,
                    ctx.http_session,
                )

                all_denom_infos = denom_infos + [
                    DenomChainInfo(
                        denom=src,
                        port=None,
                        channel=None,
                        chain_id="neutron-1",
                    )
                ]

                ctx.state.denom_cache[pair] = {
                    info.chain_id: info.denom
                    for info in all_denom_infos
                    if info.chain_id
                }

                for info in all_denom_infos:
                    if not info.chain_id:
                        continue

                    ctx.state.chain_cache[info.denom] = info.chain_id

            providers: set[Union[AuctionProvider, PoolProvider]] = {
                x
                for x in (
                    *pools.get(a, {}).values(),
                    *(
                        pools.get(a_denom, {}).values()
                        for a_denom in ctx.state.denom_cache.get(a, {}).values()
                    ),
                    *[auctions.get(a, {}).values()],
                    *(
                        auctions.get(a, {}).values()
                        for a_denom in ctx.state.denom_cache.get(a, {}).values()
                    ),
                )
                if x
            }

            return (
                Leg(
                    (
                        prov.asset_a
                        if prov.asset_a() == a
                        or prov.asset_a() in ctx.state.denom_cache.get(a)
                        else prov.asset_b
                    ),
                    (
                        prov.asset_b
                        if prov.asset_a() == a
                        or prov.asset_a() in ctx.state.denom_cache.get(a)
                        else prov.asset_a
                    ),
                    prov,
                )
                for prov in providers
            )

        for provider in await providers_for(pair):
            edges_for = ctx.state.weights.get(provider, {})
            match_pair = provider.out_asset()

            if not edges_for:
                continue

            for match_pair, edges in edges_for.items():
                # Get all pools that convert between these two assets
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
