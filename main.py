from src.scheduler import Scheduler
from src.util import deployments
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.contracts.pool.astroport import AstroportPoolDirectory
from src.strategies.naive import strategy
import schedule
import argparse
import logging
import sys

logger = logging.getLogger(__name__)


def main():
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)

    parser = argparse.ArgumentParser(
        prog="arbbot",
        description="Identifies and executes arbitrage opportunities between Valence, Osmosis, and Astroport.",
    )
    parser.add_argument("-p", "--poll_interval", default=120)

    args = parser.parse_args()

    logger.info("Building pool catalogue")

    sched = Scheduler(strategy)

    # Register Osmosis and Astroport providers
    osmosis = OsmosisPoolDirectory()
    astro = AstroportPoolDirectory(deployments())

    osmo_pools = osmosis.pools()
    astro_pools = astro.pools()

    for base in osmo_pools.values():
        for pool in base.values():
            sched.register_provider(pool)

    for base in astro_pools.values():
        for pool in base.values():
            sched.register_provider(pool)

    # Continuously poll the strategy on the specified interval
    schedule.every(args.poll_interval).seconds.do(lambda _: sched.poll())
    sched.poll()


if __name__ == "__main__":
    main()
