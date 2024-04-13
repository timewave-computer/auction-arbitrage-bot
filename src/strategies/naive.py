from src.contracts.pool.provider import PoolProvider
from src.contracts.auction import AuctionProvider
from src.scheduler import Ctx
from src.util import denom_on_chain
from typing import List, Union, Optional, Self
from datetime import time, datetime
from collections import deque
import logging

logger = logging.getLogger(__name__)


class State:
    last_discovered: Optional[datetime]
    routes: List[List[Union[PoolProvider, AuctionProvider]]]

    def __init__(self) -> None:
        self.last_discovered = None
        self.routes = []

    def poll(
        self,
        ctx: Ctx,
        pools: dict[str, dict[str, List[PoolProvider]]],
        auctions: dict[str, dict[str, AuctionProvider]],
    ) -> Self:
        # No need to update the state
        if (
            self.last_discovered is not None
            and (datetime.now() - self.last_discovered).total_seconds()
            < ctx.discovery_interval
        ):
            return self

        # Store all built routes
        vertices = sum(
            (len(pool_set) for base in pools.values() for pool_set in base.values())
        ) + sum((1 for base in auctions.values() for _ in base.values()))

        # Perform a breadth-first traversal, exploring all possible
        # routes with increasing hops
        logger.info(
            f"Building route tree from {ctx.base_denom} with {vertices} vertices (this may take a while)"
        )

        self.routes: List[List[Union[PoolProvider, AuctionProvider]]] = (
            get_routes_with_depth_bfs(ctx.max_hops, ctx.base_denom, pools, auctions)
        )

        logger.info(
            f"Finished building route tree; discovered {len(self.routes)} routes"
        )
        self.last_discovered = datetime.now()

        return self


def strategy(
    ctx: Ctx,
    pools: dict[str, dict[str, List[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
) -> Ctx:
    if ctx.state is None:
        ctx.state = State()

    state = ctx.state.poll(ctx, pools, auctions)

    for route in ctx.state.routes:
        logger.info(
            f"Discovered route with {len(route)} hop(s): {' -> '.join(map(lambda route_leg : route_leg.asset_a() + ' - ' + route_leg.asset_b(), route))}"
        )

    return ctx.with_state(state)


def get_routes_with_depth_bfs(
    depth: int,
    src: str,
    pools: dict[str, dict[str, List[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
) -> List[List[Union[PoolProvider, AuctionProvider]]]:
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
            for pool_set in pools[src].values()
            for pool in pool_set
        ]
    )
    paths: List[List[Union[PoolProvider, AuctionProvider]],] = []
    denom_cache = {}

    # Perform a breadth-first traversal, exploring all possible
    # routes from `src` back to itself
    while len(to_explore) > 0:
        pool, path, base_denom = to_explore.pop()

        pair_denom = pool.asset_a() if pool.asset_a() != base_denom else pool.asset_b()

        if pair_denom not in denom_cache:
            denom_cache[pair_denom] = denom_on_chain(
                "neutron-1", pair_denom, "osmosis-1"
            )

        pair_denom_osmo = denom_cache[pair_denom]

        pair_pools = (
            pool for pool_set in pools[pair_denom].values() for pool in pool_set
        )

        if len(path[0]) + 1 > depth:
            continue

        if pair_denom == src and len(path[0]) > 0:
            path[0].append(pool)
            path[1].add(pool)

            logger.info(f"Closed circuit from {src} to {pair_denom}; registering route")
            logger.info(
                f"Discovered route with {len(path[0])} hop(s): {' -> '.join(map(lambda route_leg : route_leg.asset_a() + ' - ' + route_leg.asset_b(), path[0]))}"
            )

            paths.append(path[0])

            continue

        for candidate_pool in pair_pools:
            if candidate_pool not in path[1] and candidate_pool != pool:
                to_explore.append(
                    (candidate_pool, (path[0] + [pool], path[1] | {pool}), pair_denom)
                )

        # Osmosis may not have a matching pool
        if pair_denom_osmo in pools:
            pair_pools_osmo = (
                (
                    pool
                    for pool_set in pools[pair_denom_osmo].values()
                    for pool in pool_set
                )
                if pair_denom_osmo is not None
                else []
            )

            for candidate_pool in pair_pools_osmo:
                if candidate_pool not in path[1] and pool != candidate_pool:
                    to_explore.append(
                        (
                            candidate_pool,
                            (path[0] + [pool], path[1] | {pool}),
                            pair_denom,
                        )
                    )

        # Neither might the auctions
        if pair_denom in auctions:
            pair_pools_auction = auctions[pair_denom].values()

            for candidate_auction in pair_pools_auction:
                if candidate_auction not in path[1] and pool != candidate_auction:
                    to_explore.append(
                        (
                            candidate_auction,
                            (path[0] + [pool], path[1] | {pool}),
                            pair_denom,
                        )
                    )

    return paths
