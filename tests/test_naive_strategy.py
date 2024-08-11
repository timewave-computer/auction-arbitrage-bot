"""
Tests the naive strategy.
"""

import typing
from typing import Any
from dataclasses import dataclass
import json
from src.util import custom_neutron_network_config
from src.scheduler import Ctx
from src.strategies.util import fmt_route_leg, IBC_TRANSFER_GAS
from src.strategies.naive import State, route_gas
from src.contracts.pool.osmosis import OsmosisPoolDirectory
from src.contracts.route import Leg
from src.contracts.pool.astroport import NeutronAstroportPoolDirectory
from src.contracts.auction import AuctionDirectory
from src.util import DISCOVERY_CONCURRENCY_FACTOR
from tests.util import deployments
from cosmpy.aerial.client import LedgerClient
from cosmpy.aerial.wallet import LocalWallet
import pytest
import aiohttp
import grpc


pytest_plugins = ("pytest_asyncio",)


@dataclass
class MockPool:
    swap_fee: int
    chain_id: str


@pytest.mark.asyncio
async def test_fmt_route_leg() -> None:
    """
    Test that the utility function for formatting a route leg behaves as
    expected.
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        # Register Osmosis and Astroport providers
        osmosis = OsmosisPoolDirectory(deployments(), session)
        pool = list(list((await osmosis.pools()).values())[0].values())[0]

        # Check that a route leg can be formatter
        assert fmt_route_leg(Leg(pool.asset_a, pool.asset_b, pool)) == "osmosis"

        # Check that astroport legs can be formatted
        astro = NeutronAstroportPoolDirectory(
            deployments(),
            "neutron-1",
            session,
            [
                grpc.aio.secure_channel(
                    "neutron-grpc.publicnode.com:443",
                    grpc.ssl_channel_credentials(),
                )
            ],
        )
        astro_pool = list(list((await astro.pools()).values())[0].values())[0]

        assert (
            fmt_route_leg(Leg(astro_pool.asset_a, astro_pool.asset_b, astro_pool))
            == "astroport (neutron-1)"
        )

        # Check that valence auctions can be formatted
        valence = AuctionDirectory(
            deployments(),
            session,
            [
                grpc.aio.secure_channel(
                    "neutron-grpc.publicnode.com:443",
                    grpc.ssl_channel_credentials(),
                )
            ],
        )
        auction = list(list((await valence.auctions()).values())[0].values())[0]

        assert (
            fmt_route_leg(Leg(auction.asset_a, auction.asset_b, auction)) == "auction"
        )


@pytest.mark.asyncio
async def test_state_poll() -> None:
    net_config, deployments = (None, None)

    with open("net_conf.json", "r", encoding="utf-8") as nf:
        net_config = json.load(nf)

    with open("contracts/deployments.json", "r", encoding="utf-8") as f:
        deployments = json.load(f)

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(force_close=True, limit_per_host=1),
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
                for chain_id, endpoints in net_config.items()
            },
            net_config,
            LocalWallet.from_mnemonic(
                "decorate bright ozone fork gallery riot bus exhaust worth way bone indoor calm squirrel merry zero scheme cotton until shop any excess stage laundry",
                prefix="neutron",
            ),
            {"base_denom": "untrn"},
            None,
            False,
            session,
            [],
            deployments,
            None,
        )

        s = State(None)
        s.poll(ctx, {}, {})

        assert s.balance
        assert s.balance > 0


@typing.no_type_check
def test_route_gas() -> None:
    r_atomic = [
        Leg(
            None,
            None,
            MockPool(swap_fee=100, chain_id="neutron-1"),
        )
    ] * 3

    r_ibc = [
        Leg(
            None,
            None,
            MockPool(swap_fee=100, chain_id="neutron-1"),
        ),
        Leg(
            None,
            None,
            MockPool(swap_fee=100, chain_id="osmosis-1"),
        ),
        Leg(
            None,
            None,
            MockPool(swap_fee=100, chain_id="neutron-1"),
        ),
    ]

    assert route_gas(r_atomic) == 300
    assert route_gas(r_ibc) == 300 + IBC_TRANSFER_GAS * 2
