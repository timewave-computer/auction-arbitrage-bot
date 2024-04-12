from src.scheduler import Scheduler, Ctx
from src.util import deployments
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.contracts.pool.astroport import AstroportPoolDirectory
from src.strategies.naive import strategy
import schedule
import argparse
import logging
import sys

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)

    parser = argparse.ArgumentParser(
        prog="arbbot",
        description="Identifies and executes arbitrage opportunities between Valence, Osmosis, and Astroport.",
    )
    parser.add_argument("-p", "--poll_interval", default=120)
    parser.add_argument("-m", "--max_hops", default=5)
    parser.add_argument(
        "-b",
        "--base_symbol",
        default="USDC",
    )

    args = parser.parse_args()

    logger.info("Building pool catalogue")

    ctx = Ctx(args.poll_interval, args.max_hops, args.base_symbol)
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

    logger.info(f"Built pool catalogue with {n_pools} pools")

    # Continuously poll the strategy on the specified interval
    schedule.every(args.poll_interval).seconds.do(lambda _: sched.poll())
    sched.poll()

    while True:
        schedule.run_pending()


if __name__ == "__main__":
    main()
