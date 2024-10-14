"""
Implements utilities for implementing arbitrage bots.
"""

import asyncio
from base64 import standard_b64encode
import json
from decimal import Decimal
from typing import Any, Optional, Callable, TypeVar
from functools import cached_property
from dataclasses import dataclass
import aiohttp
import grpc
from cosmpy.aerial.client import NetworkConfig, LedgerClient
from cosmpy.aerial.contract import LedgerContract
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.aerial.tx_helpers import SubmittedTx
from cosmwasm.wasm.v1 import query_pb2, query_pb2_grpc


DENOM_RESOLVER_TIMEOUT_SEC = 5


# Dictates the maximum number of concurrent calls to the skip
# API in searching
DISCOVERY_CONCURRENCY_FACTOR = 15


# Dictates the maximum number of concurrent calls to pool providers
# for profit simulation (evaluation)
EVALUATION_CONCURRENCY_FACTOR = 10


NEUTRON_NETWORK_CONFIG = NetworkConfig(
    chain_id="neutron-1",
    url="grpc+http://grpc-kralum.neutron-1.neutron.org:80",
    fee_minimum_gas_price=0.0053,
    fee_denomination="untrn",
    staking_denomination="untrn",
)


IBC_TRANSFER_TIMEOUT_SEC = 20

IBC_TRANSFER_POLL_INTERVAL_SEC = 5


def custom_neutron_network_config(
    url: str, chain_id: Optional[str] = "neutron-1"
) -> NetworkConfig:
    """
    Creates a neutron client NetworkConfig with a specific RPC URL.
    """

    return NetworkConfig(
        chain_id=chain_id,
        url=url,
        fee_minimum_gas_price=0.0053,
        fee_denomination="untrn",
        staking_denomination="untrn",
    )


async def try_multiple_rest_endpoints(
    endpoints: list[str],
    route: str,
    session: aiohttp.ClientSession,
    json_body: Optional[dict[str, Any]] = None,
) -> Optional[Any]:
    """
    Returns the response from the first queried endpoint that responds successfully.
    """

    for endpoint in endpoints:
        try:
            async with session.get(
                f"{endpoint}{route}",
                headers={
                    "accept": "application/json",
                    "content-type": "application/json",
                },
                json=json_body,
            ) as resp:
                if resp.status != 200:
                    continue

                return await resp.json()
        except (
            aiohttp.client_exceptions.ClientOSError,
            aiohttp.client_exceptions.ServerDisconnectedError,
            asyncio.TimeoutError,
        ):
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


async def try_query_multiple(
    providers: list[LedgerContract],
    query: Any,
    session: aiohttp.ClientSession,
    chans: list[grpc.aio.Channel],
) -> Optional[Any]:
    """
    Attempts to query the first LedgerContract in the list, falling back to
    further providers.
    """

    ser_query = json.dumps(query).encode("utf-8")

    for prov in providers:
        if prov._client._network_config.url.startswith("rest+"):
            async with session.get(
                f"{prov._client._network_config.url}/cosmwasm/wasm/v1/contract/{prov.address}/smart/{standard_b64encode(ser_query).decode('utf-8')}"
            ) as resp:
                if resp.status != 200:
                    continue

                try:
                    return await resp.json()
                except (RuntimeError, ValueError, grpc.aio._call.AioRpcError):
                    continue

            continue

    for chan in chans:
        try:
            stub = query_pb2_grpc.QueryStub(chan)
            resp_grpc = await stub.SmartContractState(
                query_pb2.QuerySmartContractStateRequest(
                    address=providers[0].address, query_data=ser_query
                )
            )

            if not resp_grpc:
                continue

            return json.loads(resp_grpc.data)
        except (RuntimeError, ValueError, grpc.aio._call.AioRpcError):
            continue

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


def int_to_decimal(n: int) -> Decimal:
    """
    Converts an expanded decimal to a decimal with 18 points
    of precision.
    """

    return Decimal(n) / (Decimal(10) ** Decimal(18))


@dataclass
class ChainInfo:
    """
    Contains basic information about a chain.
    """

    chain_name: str
    chain_id: str
    pfm_enabled: bool
    supports_memo: bool
    bech32_prefix: str
    fee_asset: str
    chain_type: str
    pretty_name: str


def load_chain_info(obj: dict[str, Any]) -> ChainInfo:
    return ChainInfo(
        chain_name=obj["chain_name"],
        chain_id=obj["chain_id"],
        pfm_enabled=obj["pfm_enabled"],
        supports_memo=obj["supports_memo"],
        bech32_prefix=obj["bech32_prefix"],
        fee_asset=obj["fee_asset"],
        chain_type=obj["chain_type"],
        pretty_name=obj["pretty_name"],
    )


@dataclass
class DenomRouteQuery:
    """
    Information identifying a request for a denom route.
    """

    src_chain: str
    src_denom: str
    dest_chain: str
    dest_denom: str

    def __hash__(self) -> int:
        return hash((self.src_chain, self.src_denom, self.dest_chain, self.dest_denom))


@dataclass
class DenomRouteLeg:
    """
    Represents an IBC transfer as a leg of a greater IBC transfer
    from some src denom on a src chain to a dest denom on a dest chain.
    """

    # The origin and destination chains
    src_chain: str
    dest_chain: str

    # The origin and destination denoms
    src_denom: str
    dest_denom: str

    # The current leg in and out chain and denoms
    from_chain: ChainInfo
    to_chain: ChainInfo

    port: str
    channel: str


def load_denom_route_leg(obj: dict[str, Any]) -> DenomRouteLeg:
    return DenomRouteLeg(
        src_chain=obj["src_chain"],
        dest_chain=obj["dest_chain"],
        src_denom=obj["src_denom"],
        dest_denom=obj["dest_denom"],
        from_chain=load_chain_info(obj["from_chain"]),
        to_chain=load_chain_info(obj["to_chain"]),
        port=obj["port"],
        channel=obj["channel"],
    )


def fmt_denom_route_leg(leg: DenomRouteLeg) -> str:
    return (
        f"{src_denom} ({src_chain}) -> {dest_denom} ({dest_chain}) via {channel}/{port}"
    )


@dataclass
class DenomChainInfo:
    """
    Represents information about a denomination
    on a particular destination chain from a
    given source chain.
    """

    denom: str
    src_chain_id: str
    dest_chain_id: str


def load_denom_chain_info(obj: dict[str, Any]) -> DenomChainInfo:
    return DenomChainInfo(
        denom=obj["denom"],
        src_chain_id=obj["src_chain_id"],
        dest_chain_id=obj["dest_chain_id"],
    )


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
