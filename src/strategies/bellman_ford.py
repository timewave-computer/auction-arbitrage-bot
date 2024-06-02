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
from asyncio import Semaphore
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
from src.contracts.route import Leg, Status
from src.scheduler import Ctx
from src.strategies.util import (
    fmt_route,
    exec_arb,
    route_base_denom_profit,
    quantities_for_route_profit,
    starting_quantity_for_route_profit,
)
from src.util import (
    MAX_SKIP_CONCURRENT_CALLS,
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

    def __hash__(self) -> int:
        return hash((hash(self.backend), hash(self.weight)))


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

        sem = asyncio.Semaphore(DISCOVERY_CONCURRENCY_FACTOR)

        async def all_edges_for(
            vertex: Union[AuctionProvider, PoolProvider]
        ) -> tuple[
            Union[AuctionProvider, PoolProvider], Optional[Edge], Optional[Edge]
        ]:
            async with sem:
                edge_a_b = await pair_provider_edge(
                    vertex.asset_a(), vertex.asset_b(), vertex
                )

                try:
                    return (
                        vertex,
                        (
                            Edge(edge_a_b[1].backend, -Decimal.ln(edge_a_b[1].weight))
                            if edge_a_b[1]
                            else None
                        ),
                        (
                            Edge(
                                Leg(
                                    edge_a_b[1].backend.out_asset,
                                    edge_a_b[1].backend.in_asset,
                                    edge_a_b[1].backend.backend,
                                ),
                                -Decimal.ln(Decimal(1) / edge_a_b[1].weight),
                            )
                            if edge_a_b[1]
                            else None
                        ),
                    )
                except asyncio.TimeoutError:
                    return (vertex, None, None)

        # Calculate all edge weights
        weights: Iterator[tuple[Union[AuctionProvider, PoolProvider], Edge, Edge]] = (
            (prov, edge_a, edge_b)
            for prov, edge_a, edge_b in (
                await asyncio.gather(*[all_edges_for(vertex) for vertex in vertices])
            )
            if edge_a and edge_b
        )

        self.weights = {prov: (edge_a, edge_b) for prov, edge_a, edge_b in weights}

        logger.info("Got %d weights", len(self.weights.values()))

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
                int_to_decimal(await provider.exchange_rate()),
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
                Decimal(balance_asset_b) / Decimal(balance_asset_a),
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
            Decimal(balance_asset_a) / Decimal(balance_asset_b),
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

    r = ctx.queue_route(route, 0, 0, [])

    ctx.log_route(r, "info", "Route queued: %s", [fmt_route(route)])

    balance_resp = try_multiple_clients(
        ctx.clients["neutron"],
        lambda client: client.query_bank_balance(
            Address(ctx.wallet.public_key(), prefix="neutron"),
            ctx.cli_args["base_denom"],
        ),
    )

    if not balance_resp:
        return ctx

    profit = await route_base_denom_profit(balance_resp, route)

    r.theoretical_profit = profit

    if profit < ctx.cli_args["profit_margin"]:
        ctx.log_route(
            r,
            "info",
            "Route is not profitable with profit of %d: %s",
            [
                profit,
                fmt_route(route),
            ],
        )

        return ctx

    starting_amt = await starting_quantity_for_route_profit(balance_resp, route, r, ctx)

    profit, quantities = await quantities_for_route_profit(
        starting_amt,
        route,
        r,
        ctx,
    )

    r.expeted_profit = profit
    r.quantities = quantities

    if profit < ctx.cli_args["profit_margin"]:
        logger.debug(
            "Route is not profitable with profit of %d: %s",
            profit,
            fmt_route(route),
        )

        return ctx

    logger.info("Executing route with profit of %d: %s", profit, fmt_route(route))

    try:
        await exec_arb(route, profit, quantities, route, ctx)

        r.status = Status.EXECUTED

        ctx.log_route(r, "info", "Executed route successfully: %s", [fmt_route(route)])
    except Exception as e:
        ctx.log_route(r, "error", "Arb failed %s: %s", [fmt_route(route), e])

        r.status = Status.FAILED
    finally:
        ctx.update_route(r)

        ctx = ctx.with_state(ctx.state.poll(ctx, pools, auctions)).commit_history()

    logger.info("Completed arbitrage round")

    return ctx


async def providers_for(a: str) -> Iterator[Leg]:
    """
    Gets all legs that start at src.
    """

    if not a in ctx.state.denom_cache:
        async with sem:
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
                chain_id=(
                    "neutron-1"
                    if not a in ctx.state.chain_cache
                    else ctx.state.chain_cache[a]
                ),
            )
        ]

        ctx.state.denom_cache[pair] = {
            info.chain_id: info.denom for info in all_denom_infos if info.chain_id
        }

        for info in all_denom_infos:
            if not info.chain_id:
                continue

            ctx.state.chain_cache[info.denom] = info.chain_id

    providers: set[Union[AuctionProvider, PoolProvider]] = {
        x
        for x in (
            *(pool for pool_set in pools.get(a, {}).values() for pool in pool_set),
            *(
                pool
                for a_denom in ctx.state.denom_cache.get(a, {}).values()
                for pool_set in pools.get(a_denom, {}).values()
                for pool in pool_set
            ),
            *auctions.get(a, {}).values(),
            *(
                auction
                for a_denom in ctx.state.denom_cache.get(a, {}).values()
                for auction in auctions.get(a_denom, {}).values()
            ),
        )
        if x
    }

    return (
        Leg(
            (
                prov.asset_a
                if prov.asset_a() == a or prov.asset_a() in ctx.state.denom_cache.get(a)
                else prov.asset_b
            ),
            (
                prov.asset_b
                if prov.asset_a() == a or prov.asset_a() in ctx.state.denom_cache.get(a)
                else prov.asset_a
            ),
            prov,
        )
        for prov in providers
    )


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

    vertices: set[Union[AuctionProvider, PoolProvider]] = {
        *(
            pool
            for pool_reg in pools.values()
            for pool_set in pool_reg.values()
            for pool in pool_set
        ),
        *(
            auction
            for auction_set in auctions.values()
            for auction in auction_set.values()
        ),
    }

    if ctx.cli_args["pools"]:
        vertices = set(random.sample(sorted(vertices), ctx.cli_args["pools"] - 1))

    # How far a given denom is from the `src` denom
    distances: dict[str, Decimal] = {}

    for vertex in vertices:
        distances[vertex.asset_a()] = Decimal("Inf")
        distances[vertex.asset_b()] = Decimal("Inf")

    pred: dict[str, str] = {}
    pair_leg: dict[tuple[str, str], Leg] = {}

    # Relax edges
    for _ in range(len(vertices)):
        for edge_a, edge_b in ctx.state.weights.values():

            def relax_edge(edge: Edge) -> None:
                if (
                    (
                        distances[edge.backend.in_asset()]
                        if distances[edge.backend.in_asset()] != Decimal("Inf")
                        else 0
                    )
                    + edge.weight
                ) < distances[edge.backend.out_asset()]:
                    distances[edge.backend.out_asset()] = (
                        distances[edge.backend.in_asset()]
                        if distances[edge.backend.in_asset()] != Decimal("Inf")
                        else 0
                    )
                    pred[edge.backend.out_asset()] = edge.backend.in_asset()
                    pair_leg[(edge.backend.in_asset(), edge.backend.out_asset())] = (
                        edge.backend
                    )

            relax_edge(edge_a)
            relax_edge(edge_b)

    cycles = []

    # Find the negative cycle from src
    for edge_a, edge_b in ctx.state.weights.values():

        def check_cycle(edge: Edge) -> Optional[list[str]]:
            if (
                distances[edge.backend.in_asset()] + edge.weight
                >= distances[edge.backend.out_asset()]
            ):
                return None

            pred[edge.backend.out_asset()] = edge.backend.in_asset()

            visited = set()
            visited.add(edge.backend.out_asset())

            curr = edge.backend.in_asset()

            while not curr in visited:
                visited.add(curr)
                curr = pred[curr]

            cycle = [curr]
            pos = pred[curr]

            while pos != curr:
                cycle = [pos] + cycle
                pos = pred[pos]

            cycle = [pos] + cycle

            return cycle

        cycle = check_cycle(edge_a)

        if cycle and len(cycle) > 3:
            cycles.append(cycle)

        cycle = check_cycle(edge_b)

        if cycle and len(cycle) > 3:
            cycles.append(cycle)

    if len(cycles) == 0:
        return None

    legs = []

    for i, asset_a in enumerate(cycles[0][: len(cycles[0]) - 1]):
        asset_b = cycles[0][i + 1]

        legs.append(pair_leg[(asset_a, asset_b)])

    return legs
