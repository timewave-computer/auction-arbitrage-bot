"""
Implements an arbitrage strategy with an arbitrary number
of hops using all available providers.
"""

import json
from typing import List, Union, Optional, Self, Any
from datetime import datetime
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
from src.util import denom_on_chain, ContractInfo, deployments, try_multiple_clients

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
                        ent["osmosis"]["pool_id"],
                        ent["osmosis"]["asset_a"],
                        ent["osmosis"]["asset_b"],
                    )

                if "neutron_astroport" in ent:
                    return NeutronAstroportPoolProvider(
                        ContractInfo(
                            deployments()["pools"]["astroport"]["neutron"],
                            ctx.clients,
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
                            ctx.clients,
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
            "grpc": ["osmosis-grpc.publicnode.com:443"],
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
            self.routes: List[List[Union[PoolProvider, AuctionProvider]]] = (
                get_routes_with_depth_limit_bfs(
                    ctx.cli_args["max_hops"],
                    ctx.cli_args["num_routes_considered"],
                    ctx.cli_args["base_denom"],
                    pools,
                    auctions,
                )
            )

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

    if ctx.state is None:
        ctx.state = State(None, None)

    state = ctx.state.poll(ctx, pools, auctions)

    profitable_routes: List[tuple[List[Union[PoolProvider, AuctionProvider]], int]] = []

    # Calculate profitability of all routes, and report them
    # to the user
    for route in ctx.state.routes:
        balance_resp = try_multiple_clients(
            ctx.clients,
            lambda client: client.query_bank_balance(
                ctx.wallet.address(), ctx.cli_args["base_denom"]
            ),
        )

        if not balance_resp:
            continue

        profit = route_base_denom_profit(
            ctx.cli_args["base_denom"],
            balance_resp,
            route,
        )

        if profit >= ctx.cli_args["profit_margin"]:
            profitable_routes.append((route, profit))

    # Report route stats to user
    logger.info(
        "Found %d profitable routes, with max profit of %d and min profit of %d",
        len(profitable_routes),
        max(profitable_routes, key=lambda route: route[1])[1],
        min(profitable_routes, key=lambda route: route[1])[1],
    )

    for i, (route, profit) in enumerate(profitable_routes):
        logger.info(
            (
                "Candidate arbitrage opportunity #%d with "
                "profit of %d and route with %d hop(s): %s"
            ),
            i + 1,
            profit,
            len(route),
            " -> ".join(
                map(
                    lambda route_leg: fmt_route_leg(route_leg)
                    + ": "
                    + route_leg.asset_a()
                    + " - "
                    + route_leg.asset_b(),
                    route,
                )
            ),
        )

    logger.info(
        "Executing %d arbitrage opportunities",
        len(profitable_routes),
    )

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

    return "unknown pool"


def route_base_denom_profit(
    base_denom: str,
    starting_amount: int,
    route: List[Union[PoolProvider, AuctionProvider]],
) -> int:
    """
    Calculates the profit that can be obtained by following the route.
    """

    prev_asset = base_denom
    quantity_received = starting_amount

    for leg in route:
        if quantity_received == 0:
            break

        if isinstance(leg, PoolProvider):
            if prev_asset == leg.asset_a():
                quantity_received = int(leg.simulate_swap_asset_a(quantity_received))
            else:
                quantity_received = int(leg.simulate_swap_asset_b(quantity_received))
        else:
            if prev_asset == leg.asset_a():
                quantity_received = leg.exchange_rate() * quantity_received

    return quantity_received - starting_amount


def get_routes_with_depth_limit_bfs(
    depth: int,
    limit: int,
    src: str,
    pools: dict[str, dict[str, List[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
) -> List[List[Union[PoolProvider, AuctionProvider]]]:
    """
    Finds `limit` routes from `src` back to `src` with a maximum route length
    of `depth`.
    """

    to_explore: deque[
        tuple[
            Union[PoolProvider, AuctionProvider],
            tuple[
                List[Union[PoolProvider, AuctionProvider]],
                set[Union[PoolProvider, AuctionProvider]],
            ],
            str,
        ]
    ] = deque(
        [
            (pool, ([], set()), src)
            for pool_set in pools.get(src, {}).values()
            for pool in pool_set
        ]
    )
    paths: List[List[Union[PoolProvider, AuctionProvider]]] = []
    denom_cache = {}

    # Perform a breadth-first traversal, exploring all possible
    # routes from `src` back to itself
    while len(to_explore) > 0 and len(paths) < limit:
        pool, path, base_denom = to_explore.pop()
        pair_denom = pool.asset_a() if pool.asset_a() != base_denom else pool.asset_b()

        if pair_denom not in denom_cache:
            denom_cache[pair_denom] = denom_on_chain(
                "neutron-1", pair_denom, "osmosis-1"
            )

        pair_denom_osmo = denom_cache[pair_denom]

        if len(path[0]) + 1 > depth:
            continue

        if pair_denom == src and len(path[0]) > 0:
            path[0].append(pool)
            path[1].add(pool)

            logger.info(
                "Closed circuit from %s to %s; registering route",
                src,
                pair_denom,
            )
            logger.info(
                "Discovered route with %d hop(s): %s",
                len(path[0]),
                " -> ".join(
                    map(
                        lambda route_leg: route_leg.asset_a()
                        + " - "
                        + route_leg.asset_b(),
                        path[0],
                    )
                ),
            )

            paths.append(path[0])

            continue

        # Check auctions first for exploration
        if pair_denom in auctions:
            for candidate_auction in auctions[pair_denom].values():
                if candidate_auction not in path[1] and pool != candidate_auction:
                    to_explore.append(
                        (
                            candidate_auction,
                            (path[0] + [pool], path[1] | {pool}),
                            pair_denom,
                        )
                    )

        for candidate_pool in (
            pool for pool_set in pools[pair_denom].values() for pool in pool_set
        ):
            if candidate_pool not in path[1] and candidate_pool != pool:
                to_explore.append(
                    (candidate_pool, (path[0] + [pool], path[1] | {pool}), pair_denom)
                )

        # Osmosis may not have a matching pool
        if pair_denom_osmo in pools:
            for candidate_pool in (
                (
                    pool
                    for pool_set in pools[pair_denom_osmo].values()
                    for pool in pool_set
                )
                if pair_denom_osmo is not None
                else []
            ):
                if candidate_pool not in path[1] and pool != candidate_pool:
                    to_explore.append(
                        (
                            candidate_pool,
                            (path[0] + [pool], path[1] | {pool}),
                            pair_denom,
                        )
                    )

    return paths
