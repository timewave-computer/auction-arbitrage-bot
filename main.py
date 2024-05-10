"""
Implements a command-line interface for running arbitrage strategies.
"""

import json
import argparse
import logging
import sys
from os import path
import schedule
from cosmpy.aerial.client import LedgerClient, NetworkConfig  # type: ignore
from cosmpy.aerial.wallet import LocalWallet  # type: ignore
from src.scheduler import Scheduler, Ctx
from src.util import deployments, NEUTRON_NETWORK_CONFIG, custom_neutron_network_config
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.contracts.pool.astroport import NeutronAstroportPoolDirectory
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
            opportunities between Neutron and Osmosis via Astroport and Valence.""",
    )
    parser.add_argument("-f", "--pool_file", default=None)
    parser.add_argument("-p", "--poll_interval", default=120)
    parser.add_argument("-d", "--discovery_interval", default=600)
    parser.add_argument("-nh", "--hops", default=3)
    parser.add_argument("-n", "--num_routes_considered", default=30)
    parser.add_argument(
        "-r",
        "--require_leg_types",
        nargs="*",
        default=[],
    )
    parser.add_argument(
        "-b",
        "--base_denom",
        default="ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81",
    )
    parser.add_argument(
        "-pm",
        "--profit_margin",
        default=1000,
    )
    parser.add_argument(
        "-w",
        "--wallet_mnemonic",
    )
    parser.add_argument("-c", "--net_config")
    parser.add_argument("cmd", nargs="?", default=None)

    args = parser.parse_args()

    # If the user specified a poolfile, create the poolfile if it is empty
    if args.pool_file is not None and not path.isfile(args.pool_file):
        logger.info("Creating pool file")

        with open(args.pool_file, "w+", encoding="utf-8") as f:
            json.dump(
                {},
                f,
            )

    # The user may want to use custom RPC providers
    endpoints: dict[str, dict[str, list[str]]] = {
        "neutron": {"http": [], "grpc": []},
        "osmosis": {
            "http": ["https://lcd.osmosis.zone"],
            "grpc": ["grpc+https://osmosis-grpc.publicnode.com:443"],
        },
    }

    if args.net_config is not None:
        logger.info("Applying net config")

        with open(args.net_config, "r", encoding="utf-8") as f:
            endpoints = json.load(f)

    logger.info("Building pool catalogue")

    ctx = Ctx(
        {
            "neutron": [
                LedgerClient(NEUTRON_NETWORK_CONFIG),
                *[
                    LedgerClient(custom_neutron_network_config(endpoint))
                    for endpoint in endpoints["neutron"]["http"]
                ],
            ],
            "osmosis": [
                *[
                    LedgerClient(
                        NetworkConfig(
                            chain_id="osmosis-1",
                            url=endpoint,
                            fee_minimum_gas_price=0.0053,
                            fee_denomination="uosmo",
                            staking_denomination="uosmo",
                        )
                    )
                    for endpoint in endpoints["osmosis"]["grpc"]
                ],
            ],
        },
        endpoints,
        LocalWallet.from_mnemonic(args.wallet_mnemonic, prefix="neutron"),
        {
            "pool_file": args.pool_file,
            "poll_interval": int(args.poll_interval),
            "discovery_interval": int(args.discovery_interval),
            "hops": int(args.hops),
            "num_routes_considered": int(args.num_routes_considered),
            "require_leg_types": args.require_leg_types,
            "base_denom": args.base_denom,
            "profit_margin": int(args.profit_margin),
            "wallet_mnemonic": args.wallet_mnemonic,
            "cmd": args.cmd,
            "net_config": args.net_config,
        },
        None,
        False,
    )
    sched = Scheduler(ctx, strategy)

    # Register Osmosis and Astroport providers
    osmosis = OsmosisPoolDirectory(
        poolfile_path=args.pool_file,
        endpoints=endpoints["osmosis"],
    )
    astro = NeutronAstroportPoolDirectory(
        deployments(),
        poolfile_path=args.pool_file,
        network_configs=[
            *[
                custom_neutron_network_config(endpoint)
                for endpoint in endpoints["neutron"]["http"]
            ],
            *[
                custom_neutron_network_config(endpoint)
                for endpoint in endpoints["neutron"]["grpc"]
            ],
        ],
    )

    osmo_pools = osmosis.pools()
    astro_pools = astro.pools()

    # Save pools to the specified file if the user wants to dump pools
    if args.cmd is not None:
        if args.cmd == "dump":
            # The user wants to dump to a nonexistent file
            if args.pool_file is None:
                logger.error("Dump command provided but no poolfile specified.")

                sys.exit(1)

            with open(args.pool_file, "r+", encoding="utf-8") as f:
                f.seek(0)
                json.dump(
                    {
                        "pools": {
                            "osmosis": OsmosisPoolDirectory.dump_pools(osmo_pools),
                            "neutron_astroport": NeutronAstroportPoolDirectory.dump_pools(
                                astro_pools
                            ),
                        },
                        "auctions": sched.auction_manager.dump_auctions(sched.auctions),
                    },
                    f,
                )

    for osmo_base in osmo_pools.values():
        for osmo_pool in osmo_base.values():
            sched.register_provider(osmo_pool)

    for astro_base in astro_pools.values():
        for astro_pool in astro_base.values():
            sched.register_provider(astro_pool)

    # Calculate the number of pools by summing up the number of pools for a particular base
    # in Osmosis and Astroport
    n_pools: int = sum(map(lambda base: len(base.values()), osmo_pools.values())) + sum(
        map(lambda base: len(base.values()), astro_pools.values())
    )

    logger.info("Built pool catalogue with %d pools", n_pools)

    # Continuously poll the strategy on the specified interval
    schedule.every(args.poll_interval).seconds.do(sched.poll)
    sched.poll()

    while not sched.ctx.terminated:
        schedule.run_pending()


if __name__ == "__main__":
    main()
