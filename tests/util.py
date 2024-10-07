from typing import Any, cast, AsyncIterator
import json
import aiohttp
from dataclasses import dataclass
from contextlib import asynccontextmanager
from cosmpy.aerial.client import LedgerClient, NetworkConfig
from cosmpy.aerial.wallet import LocalWallet
from src.scheduler import Ctx
from src.util import (
    DISCOVERY_CONCURRENCY_FACTOR,
    NEUTRON_NETWORK_CONFIG,
    custom_neutron_network_config,
)


@dataclass
class State:
    balance: int


# Note: this account has no funds and is not used for anything
TEST_WALLET_MNEMONIC = (
    "update armed valve web gate shiver birth exclude curtain cotton juice property"
)


def deployments() -> dict[str, Any]:
    """
    Gets a dict of contracts to address on different networks.
    See contracts/deployments.json.
    """
    with open("contracts/deployments.json", encoding="utf-8") as f:
        return cast(dict[str, Any], json.load(f))


@asynccontextmanager
async def ctx() -> AsyncIterator[Ctx[Any]]:
    """
    Gets a default context for test schedulers.
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            force_close=True, limit_per_host=DISCOVERY_CONCURRENCY_FACTOR
        ),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
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

        with open("contracts/deployments.json", encoding="utf-8") as f:
            yield Ctx(
                clients={
                    "neutron-1": [
                        LedgerClient(NEUTRON_NETWORK_CONFIG),
                        *[
                            LedgerClient(custom_neutron_network_config(endpoint))
                            for endpoint in endpoints["neutron-1"]["grpc"]
                        ],
                    ],
                    "osmosis-1": [
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
                            for endpoint in endpoints["osmosis-1"]["grpc"]
                        ],
                    ],
                },
                endpoints=endpoints,
                wallet=LocalWallet.from_mnemonic(
                    TEST_WALLET_MNEMONIC, prefix="neutron"
                ),
                cli_args={
                    "pool_file": None,
                    "poll_interval": 120,
                    "hops": 3,
                    "pools": 100,
                    "require_leg_types": set(),
                    "base_denom": "",
                    "profit_margin": 100,
                    "wallet_mnemonic": "",
                    "cmd": "",
                    "net_config": "",
                    "log_file": "",
                    "history_file": "",
                    "skip_api_key": None,
                },
                state=None,
                terminated=False,
                http_session=session,
                order_history=[],
                deployments=cast(dict[str, Any], json.load(f)),
                denom_map={},
                denom_routes={},
                chain_info={},
            ).with_state(State(1000))
