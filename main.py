"""
Implements a command-line interface for running arbitrage strategies.
"""

import argparse
import logging
import sys
import schedule
from cosmpy.aerial.client import LedgerClient  # type: ignore
from src.scheduler import Scheduler, Ctx
from src.util import deployments, NEUTRON_NETWORK_CONFIG
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.contracts.pool.astroport import AstroportPoolDirectory
from src.strategies.naive import strategy

logger = logging.getLogger(__name__)


def main() -> None:
    """
    Entrypoint for the arbitrage bot.
    """

    logging.basicConfig(stream=sys.stdout, level=logging.INFO)

    parser = argparse.ArgumentParser(
        prog="arbbot",
        description="""Identifies and executes arbitrage
            opportunities between Valence, Osmosis, and Astroport.""",
    )
    parser.add_argument("-p", "--poll_interval", default=120)
    parser.add_argument("-d", "--discovery_interval", default=600)
    parser.add_argument("-m", "--max_hops", default=3)
    parser.add_argument("-n", "--num_routes_considered", default=30)
    parser.add_argument(
        "-b",
        "--base_denom",
        default="ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81",
    )
    parser.add_argument(
        "-pm",
        "--profit_margin",
        default=10,
    )
    parser.add_argument(
        "-w",
        "--wallet_address",
    )

    args = parser.parse_args()

    logger.info("Building pool catalogue")

    ctx = Ctx(
        LedgerClient(NEUTRON_NETWORK_CONFIG),
        {
            "poll_interval": int(args.poll_interval),
            "discovery_interval": int(args.discovery_interval),
            "max_hops": int(args.max_hops),
            "num_routes_considered": int(args.num_routes_considered),
            "base_denom": args.base_denom,
            "profit_margin": int(args.profit_margin),
            "wallet_address": args.wallet_address,
        },
        None,
    )
    sched = Scheduler(ctx, strategy)

    # Register Osmosis and Astroport providers
    osmosis = OsmosisPoolDirectory()
    astro = AstroportPoolDirectory(deployments())

    osmo_pools = osmosis.pools()
    astro_pools = astro.pools()

    for osmo_base in osmo_pools.values():
        for osmo_pool in osmo_base.values():
            sched.register_provider(osmo_pool)

    for astro_base in astro_pools.values():
        for astro_pool in astro_base.values():
            sched.register_provider(astro_pool)

    n_pools: int = sum(map(lambda base: len(base.values()), osmo_pools.values())) + sum(
        map(lambda base: len(base.values()), astro_pools.values())
    )

    logger.info("Built pool catalogue with {n_pools} pools", n_pools=n_pools)

    # Continuously poll the strategy on the specified interval
    schedule.every(args.poll_interval).seconds.do(sched.poll)
    sched.poll()

    while True:
        schedule.run_pending()


if __name__ == "__main__":
    main()
