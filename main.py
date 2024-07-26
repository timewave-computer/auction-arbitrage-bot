#!/usr/bin/env python3

"""
Implements a command-line interface for running arbitrage strategies.
"""

import traceback
import asyncio
from multiprocessing import Process
import json
import argparse
import logging
import sys
from os import path
import os
from typing import Any, cast, Optional
from cosmpy.aerial.client import LedgerClient
from cosmpy.aerial.wallet import LocalWallet
from src.scheduler import Scheduler, Ctx
from src.util import (
    custom_neutron_network_config,
    DISCOVERY_CONCURRENCY_FACTOR,
)
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.contracts.pool.astroport import NeutronAstroportPoolDirectory
from src.contracts.route import Status
from src.strategies.naive import strategy
from dotenv import load_dotenv
import aiohttp
import grpc

logger = logging.getLogger(__name__)


async def main() -> None:
    """
    Entrypoint for the arbitrage bot.
    """

    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="arbbot",
        description="""Identifies and executes arbitrage
            opportunities between Neutron and Osmosis via Astroport and Valence.""",
    )
    parser.add_argument("-f", "--pool_file", default=None)
    parser.add_argument("--denom_file", default=None)
    parser.add_argument("-p", "--poll_interval", default=600)
    parser.add_argument("-nh", "--hops", default=3)
    parser.add_argument("-np", "--pools", default=None)
    parser.add_argument(
        "-r",
        "--require_leg_types",
        nargs="*",
        default=[],
    )
    parser.add_argument(
        "-b",
        "--base_denom",
        default="untrn",
    )
    parser.add_argument(
        "-pm",
        "--profit_margin",
        default=0,
    )
    parser.add_argument(
        "-l",
        "--log_file",
    )
    parser.add_argument("-hf", "--history_file", default="arbs.json")
    parser.add_argument("-c", "--net_config", default="net_conf.json")
    parser.add_argument(
        "-df", "--deployments_file", default="contracts/deployments.json"
    )
    parser.add_argument("cmd", nargs="*", default=None)

    args = parser.parse_args()

    if args.log_file:
        logging.basicConfig(
            format="%(asctime)s %(levelname)-8s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            filename=args.log_file,
            level=os.environ.get("LOGLEVEL", "INFO").upper(),
        )

    else:
        logging.basicConfig(
            format="%(asctime)s %(levelname)-8s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            stream=sys.stdout,
            level=os.environ.get("LOGLEVEL", "INFO").upper(),
        )

    # Always make sure the history file exists
    if args.history_file is not None and not path.isfile(args.history_file):
        logger.info("Creating pool file")

        with open(args.history_file, "w+", encoding="utf-8") as f:
            json.dump(
                [],
                f,
            )

    denom_map: Optional[dict[str, list[dict[str, str]]]] = None

    # If the user has specified a denom map, use that instead of skip
    if args.denom_file is not None and path.isfile(args.denom_file):
        with open(args.denom_file, "r", encoding="utf-8") as f:
            denom_map = json.load(f)

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
        "neutron-1": {
            "http": ["https://neutron-rest.publicnode.com"],
            "grpc": ["grpc+https://neutron-grpc.publicnode.com:443"],
        },
        "osmosis-1": {
            "http": ["https://lcd.osmosis.zone"],
            "grpc": ["grpc+https://osmosis-grpc.publicnode.com:443"],
        },
    }

    if args.net_config is not None:
        logger.info("Applying net config")

        with open(args.net_config, "r", encoding="utf-8") as f:
            endpoints = json.load(f)

    logger.info("Building pool catalogue")

    with open(args.deployments_file, encoding="utf-8") as f:
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(
                force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
            ),
            timeout=aiohttp.ClientTimeout(total=30),
        ) as session:
            ctx: Ctx[Any] = Ctx(
                {
                    chain_id: [
                        LedgerClient(
                            custom_neutron_network_config(endpoint, chain_id=chain_id)
                        )
                        for endpoint in endpoints["grpc"]
                    ]
                    for chain_id, endpoints in endpoints.items()
                },
                endpoints,
                LocalWallet.from_mnemonic(
                    os.environ.get("WALLET_MNEMONIC"), prefix="neutron"
                ),
                {
                    "pool_file": args.pool_file,
                    "poll_interval": int(args.poll_interval),
                    "hops": int(args.hops),
                    "pools": int(args.pools) if args.pools else None,
                    "require_leg_types": args.require_leg_types,
                    "base_denom": args.base_denom,
                    "profit_margin": int(args.profit_margin),
                    "wallet_mnemonic": os.environ.get("WALLET_MNEMONIC"),
                    "cmd": args.cmd,
                    "net_config": args.net_config,
                    "log_file": args.log_file,
                    "history_file": args.history_file,
                    "skip_api_key": (
                        os.environ.get("SKIP_API_KEY")
                        if "SKIP_API_KEY" in os.environ
                        else None
                    ),
                },
                None,
                False,
                session,
                [],
                cast(dict[str, Any], json.load(f)),
                denom_map,
            ).recover_history()
            sched = Scheduler(ctx, strategy)

            # Register Osmosis and Astroport providers
            osmosis = OsmosisPoolDirectory(
                ctx.deployments,
                ctx.http_session,
                poolfile_path=args.pool_file,
                endpoints=endpoints[
                    list(ctx.deployments["pools"]["osmosis"].keys())[0]
                ],
            )
            astros = [
                NeutronAstroportPoolDirectory(
                    ctx.deployments,
                    chain_id,
                    ctx.http_session,
                    [
                        (
                            grpc.aio.secure_channel(
                                endpoint.split("grpc+https://")[1],
                                grpc.ssl_channel_credentials(),
                            )
                            if "https" in endpoint
                            else grpc.aio.insecure_channel(
                                endpoint.split("grpc+http://")[1],
                            )
                        )
                        for endpoint in endpoints[chain_id]["grpc"]
                    ],
                    poolfile_path=args.pool_file,
                    endpoints=endpoints[chain_id],
                )
                for chain_id in ctx.deployments["pools"]["astroport"].keys()
            ]

            osmo_pools = await osmosis.pools()
            astros_pools = [await astro.pools() for astro in astros]

            if args.cmd is not None and len(args.cmd) > 0 and args.cmd[0] == "hist":
                # The user wnats to see a specific route
                if len(args.cmd) == 3 and args.cmd[1] == "show":
                    order_id = int(args.cmd[2])

                    if order_id < 0 or order_id >= len(ctx.order_history):
                        logger.critical("Route does not exist.")

                        sys.exit(1)

                    logger.info("%s", ctx.order_history[order_id].fmt_pretty())

                    logger.info("Execution trace:")

                    for log in ctx.order_history[order_id].logs:
                        logger.info("%s", log)
                else:
                    for order in ctx.order_history:
                        logger.info(
                            "%s (%s) expected ROI: %d, realized P/L: %d, status: %s, is_osmo: %s, is_valence: %s",
                            order,
                            order.time_created,
                            order.expected_profit,
                            order.realized_profit if order.realized_profit else 0,
                            order.status,
                            any([leg.kind == "osmosis" for leg in order.route]),
                            any([leg.kind == "auction" for leg in order.route]),
                        )

                    # Print a profit summary
                    logger.info(
                        "Summary - total routes attepmted: %d, total routes completed: %d, min P/L: %d, max P/L: %d, total P/L: %d",
                        len(ctx.order_history),
                        len(
                            [
                                order
                                for order in ctx.order_history
                                if order.status == Status.EXECUTED
                            ]
                        ),
                        min(
                            [
                                order.realized_profit
                                for order in ctx.order_history
                                if order.realized_profit
                            ],
                            default=0,
                        ),
                        max(
                            [
                                order.realized_profit
                                for order in ctx.order_history
                                if order.realized_profit
                            ],
                            default=0,
                        ),
                        sum(
                            [
                                order.realized_profit
                                for order in ctx.order_history
                                if order.realized_profit
                            ]
                        ),
                    )

                    atomic_orders = [
                        order
                        for order in ctx.order_history
                        if all(
                            [
                                leg.kind == "astroport" or leg.kind == "auction"
                                for leg in order.route
                            ]
                        )
                    ]

                    ibc_orders = [
                        order
                        for order in ctx.order_history
                        if any([leg.kind == "osmosis" for leg in order.route])
                    ]

                    logger.info(
                        "Summary - total atomic routes attepmted: %d, total atomic routes completed: %d, min P/L: %d, max P/L: %d, total atomic P/L: %d",
                        len(atomic_orders),
                        len(
                            [
                                order
                                for order in atomic_orders
                                if order.status == Status.EXECUTED
                            ]
                        ),
                        min(
                            [
                                order.realized_profit
                                for order in atomic_orders
                                if order.realized_profit
                            ],
                            default=0,
                        ),
                        max(
                            [
                                order.realized_profit
                                for order in atomic_orders
                                if order.realized_profit
                            ],
                            default=0,
                        ),
                        sum(
                            [
                                order.realized_profit
                                for order in atomic_orders
                                if order.realized_profit
                            ]
                        ),
                    )
                    logger.info(
                        "Summary - total IBC routes attepmted: %d, total IBC routes completed: %d, min P/L: %d, max P/L: %d, total IBC P/L: %d",
                        len(ibc_orders),
                        len(
                            [
                                order
                                for order in ibc_orders
                                if order.status == Status.EXECUTED
                            ]
                        ),
                        min(
                            [
                                order.realized_profit
                                for order in atomic_orders
                                if order.realized_profit
                            ],
                            default=0,
                        ),
                        max(
                            [
                                order.realized_profit
                                for order in atomic_orders
                                if order.realized_profit
                            ],
                            default=0,
                        ),
                        sum(
                            [
                                order.realized_profit
                                for order in ibc_orders
                                if order.realized_profit
                            ]
                        ),
                    )

                return

            for osmo_base in osmo_pools.values():
                for osmo_pool in osmo_base.values():
                    sched.register_provider(osmo_pool)

            for astro_pools in astros_pools:
                for astro_base in astro_pools.values():
                    for astro_pool in astro_base.values():
                        sched.register_provider(astro_pool)

            await sched.register_auctions()

            # Calculate the number of pools by summing up the number of pools for a particular base
            # in Osmosis and Astroport
            n_pools: int = sum(
                map(lambda base: len(base.values()), osmo_pools.values())
            ) + sum(map(lambda base: len(base.values()), astro_pools.values()))

            logger.info("Built pool catalogue with %d pools", n_pools)

            async def event_loop() -> None:
                while True:
                    try:
                        async with asyncio.timeout(args.poll_interval):
                            await sched.poll()
                    except Exception:
                        logger.info(
                            "Arbitrage round failed: %s", traceback.format_exc()
                        )

                        continue

            def daemon() -> None:
                loop = asyncio.get_event_loop()
                loop.run_until_complete(event_loop())

            # Save pools to the specified file if the user wants to dump pools
            if args.cmd is not None and len(args.cmd) > 0 and args.cmd[0] == "daemon":
                Process(target=daemon, args=[]).run()
                logger.info("Spawned searcher daemon")

                return

            await event_loop()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
