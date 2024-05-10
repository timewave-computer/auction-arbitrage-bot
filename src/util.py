"""
Implements utilities for implementing arbitrage bots.
"""

import json
from decimal import Decimal
from typing import Any, cast, Optional, Callable, TypeVar
from functools import cached_property
from dataclasses import dataclass
import urllib3
import grpc
from cosmpy.aerial.client import NetworkConfig, LedgerClient  # type: ignore
from cosmpy.aerial.contract import LedgerContract  # type: ignore
from cosmpy.aerial.wallet import LocalWallet  # type: ignore
from cosmpy.aerial.tx_helpers import SubmittedTx  # type: ignore


NEUTRON_NETWORK_CONFIG = NetworkConfig(
    chain_id="neutron-1",
    url="grpc+http://grpc-kralum.neutron-1.neutron.org:80",
    fee_minimum_gas_price=0.0053,
    fee_denomination="untrn",
    staking_denomination="untrn",
)


IBC_TRANSFER_TIMEOUT_SEC = 5 * 60

IBC_TRANSFER_POLL_INTERVAL_SEC = 30


def custom_neutron_network_config(url: str) -> NetworkConfig:
    """
    Creates a neutron client NetworkConfig with a specific RPC URL.
    """

    return NetworkConfig(
        chain_id="neutron-1",
        url=url,
        fee_minimum_gas_price=0.0053,
        fee_denomination="untrn",
        staking_denomination="untrn",
    )


def try_multiple_rest_endpoints(
    endpoints: list[str], route: str
) -> Optional[dict[str, Any]]:
    """
    Returns the response from the first queried endpoint that responds successfully.
    """

    client = urllib3.PoolManager()

    for endpoint in endpoints:
        try:
            return cast(
                dict[str, Any],
                json.loads(
                    client.request(
                        "GET",
                        f"{endpoint}{route}",
                    ).data
                ),
            )
        except RuntimeError:
            continue
        except ValueError:
            continue

    return None


def try_multiple_grpc_endpoints(
    endpoints: list[str], method: str, request: Any
) -> Optional[Any]:
    """
    Returns the response from the first queried grpc endpoint that responds successfully.
    """

    channels = [grpc.insecure_channel(endpoint) for endpoint in endpoints]

    for channel in channels:
        try:
            return channel.unary_unary(method, None, None).with_call(request)
        except RuntimeError:
            continue
        except ValueError:
            continue

    return None


T = TypeVar("T")


def try_multiple_clients(
    clients: list[LedgerClient], f: Callable[[LedgerClient], T]
) -> Optional[T]:
    """
    Executes a lambda function on the first available client.
    """

    for client in clients:
        try:
            return f(client)
        except RuntimeError:
            continue
        except ValueError:
            continue

    return None


def try_multiple_clients_fatal(
    clients: list[LedgerClient], f: Callable[[LedgerClient], T]
) -> T:
    """
    Executes a lambda function on the first available client.
    """

    for i, client in enumerate(clients):
        try:
            return f(client)
        except RuntimeError as e:
            if i == len(clients) - 1:
                raise e

            continue
        except ValueError as e:
            if i == len(clients) - 1:
                raise e

            continue

    assert False


def try_query_multiple(providers: list[LedgerContract], query: Any) -> Optional[Any]:
    """
    Attempts to query the first LedgerContract in the list, falling back to
    further providers.
    """

    for prov in providers:
        try:
            return cast(dict[str, Any], prov.query(query))
        except RuntimeError:
            continue
        except ValueError:
            continue
        except grpc._channel._InactiveRpcError:
            continue

    return None


def try_exec_multiple_fatal(
    providers: list[LedgerContract],
    wallet: LocalWallet,
    msg: Any,
    gas_limit: Optional[int] = None,
    funds: Optional[str] = None,
) -> SubmittedTx:
    """
    Attempts to execute a message via the first LedgerContract in the list, falling
    back to further providers.

    Throws an exception if no provider can execute the message.
    """

    for i, prov in enumerate(providers):
        try:
            return prov.execute(msg, wallet, gas_limit, funds)
        except RuntimeError as e:
            if i == len(providers) - 1:
                raise e

            continue
        except ValueError as e:
            if i == len(providers) - 1:
                raise e

            continue

    assert False


def deployments() -> dict[str, Any]:
    """
    Gets a dict of contracts to address on different networks.
    See contracts/deployments.json.
    """
    with open("contracts/deployments.json", encoding="utf-8") as f:
        return cast(dict[str, Any], json.load(f))


def decimal_to_int(dec: Decimal) -> int:
    """
    Converts a cosmwasm decimal with 18 decimal places to
    a raw quantity with no decimals.
    """
    return int(dec * 10**18)


def int_to_decimal(i: int) -> Decimal:
    """
    Converts an expanded decimal quantity to a decimal.
    """

    return Decimal(i) / (Decimal("10.0") ** 18)


@dataclass
class DenomChainInfo:
    """
    Represents information about a denomination
    on a particular destination chain from a
    given source chain.
    """

    denom: str
    port: Optional[str]
    channel: Optional[str]


def denom_info(src_chain: str, src_denom: str) -> list[DenomChainInfo]:
    """
    Gets a denom's denom and channel on/to other chains.
    """

    client = urllib3.PoolManager()

    resp = client.request(
        "POST",
        "https://api.skip.money/v1/fungible/assets_from_source",
        headers={"accept": "application/json", "content-type": "application/json"},
        json={
            "allow_multi_tx": False,
            "include_cw20_assets": True,
            "source_asset_denom": src_denom,
            "source_asset_chain_id": src_chain,
            "client_id": "timewave-arb-bot",
        },
    )

    if resp.status != 200:
        return []

    dests = resp.json()["dest_assets"]

    def chain_info(info: dict[str, Any]) -> DenomChainInfo:
        info = info["assets"][0]

        if info["trace"] != "":
            parts = info["trace"].split("/")
            port, channel = parts[0], parts[1]

            return DenomChainInfo(denom=info["denom"], port=port, channel=channel)

        return DenomChainInfo(denom=info["denom"], port=None, channel=None)

    return [chain_info(info) for info in dests.values()]


def denom_info_on_chain(
    src_chain: str, src_denom: str, dest_chain: str
) -> Optional[DenomChainInfo]:
    """
    Gets a neutron denom's denom and channel on/to another chain.
    """

    client = urllib3.PoolManager()

    resp = client.request(
        "POST",
        "https://api.skip.money/v1/fungible/assets_from_source",
        headers={"accept": "application/json", "content-type": "application/json"},
        json={
            "allow_multi_tx": False,
            "include_cw20_assets": True,
            "source_asset_denom": src_denom,
            "source_asset_chain_id": src_chain,
            "client_id": "timewave-arb-bot",
        },
    )

    if resp.status != 200:
        return None

    dests = resp.json()["dest_assets"]

    if dest_chain in dests:
        info = dests[dest_chain]["assets"][0]

        if info["trace"] != "":
            port, channel = info["trace"].split("/")

            return DenomChainInfo(denom=info["denom"], port=port, channel=channel)

        return DenomChainInfo(denom=info["denom"], port=None, channel=None)

    return None


@dataclass
class ContractInfo:
    """
    Represents the information required to lazily construct a LedgerContract for
    a contract wrapper class.
    """

    deployment_info: dict[str, Any]
    clients: list[LedgerClient]
    address: str
    deployment_item: str


@dataclass
class WithContract:
    """
    Provides instantiation and methods for accessing the contract backing a provider.
    """

    contract_info: ContractInfo

    @cached_property
    def contracts(self) -> list[LedgerContract]:
        """
        Gets the LedgerContract backing the auction wrapper.
        """

        return [
            LedgerContract(
                self.contract_info.deployment_info[self.contract_info.deployment_item][
                    "src"
                ],
                client,
                address=self.contract_info.address,
            )
            for client in self.contract_info.clients
        ]
