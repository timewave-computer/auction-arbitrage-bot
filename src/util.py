"""
Implements utilities for implementing arbitrage bots.
"""

from hashlib import sha256
import asyncio
from base64 import standard_b64encode
import json
from decimal import Decimal
from typing import Any, Optional, Callable, TypeVar, TypeGuard
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


# The maximum number of concurrent connections
# that can be open to
MAX_SKIP_CONCURRENT_CALLS = 5


# Dictates the maximum number of concurrent calls to the skip
# API in searching
DISCOVERY_CONCURRENCY_FACTOR = 20


# Dictates the maximum number of concurrent calls to pool providers
# for profit simulation (evaluation)
EVALUATION_CONCURRENCY_FACTOR = 10


# The quantity of a denom below which
# it is no longer worthwhile checking for profit
DENOM_QUANTITY_ABORT_ARB = 500


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


def decimal_to_int(dec: Decimal) -> int:
    """
    Converts a cosmwasm decimal with 18 decimal places to
    a raw quantity with no decimals.
    """

    return int(dec * 10**18)


def int_to_decimal(n: int) -> Decimal:
    """
    Converts an expanded decimal to a decimal with 18 points
    of precision.
    """

    return Decimal(n) / (Decimal(10) ** Decimal(18))


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
    chain_id: Optional[str]


async def denom_info(
    src_chain: str,
    src_denom: str,
    session: aiohttp.ClientSession,
    endpoints: dict[str, dict[str, list[str]]],
) -> list[DenomChainInfo]:
    """
    Gets a denom's denom and channel on/to other chains.
    """

    def is_not_none(info: Optional[DenomChainInfo]) -> TypeGuard[DenomChainInfo]:
        return info is not None

    return list(
        filter(
            is_not_none,
            [
                await denom_info_on_chain(
                    src_chain, src_denom, chain_id, session, endpoints
                )
                for chain_id in endpoints.keys()
            ],
        )
    )


async def denom_info_on_chain(
    src_chain: str,
    src_denom: str,
    dest_chain: str,
    session: aiohttp.ClientSession,
    endpoints: dict[str, dict[str, list[str]]],
) -> Optional[DenomChainInfo]:
    """
    Gets a neutron denom's denom and channel on/to another chain.
    """

    # Check if this denom is from another chain
    if "ibc" in src_denom:
        _, denom_hash = src_denom.split("/")
        trace = await try_multiple_rest_endpoints(
            endpoints[dest_chain]["http"],
            f"/ibc/apps/transfer/v1/denom_traces/{denom_hash}",
            session,
        )

        if not trace:
            return None

        base_denom = trace["denom_trace"]["base_denom"]
        path = trace["denom_trace"]["base_denom"]

        port_name, channel_id = path.split("/")

        return DenomChainInfo(base_denom, port_name, channel_id, dest_chain)

    # Get all channels with the other chain
    channels = []
    next_key = ""

    while next_key is not None:
        maybe_channels = await try_multiple_rest_endpoints(
            endpoints[src_chain]["http"],
            f"/ibc/core/channel/v1/channels?pagination.key={next_key}",
            session,
        )

        if not maybe_channels:
            break

        channels.extend(maybe_channels["channels"])

        if (
            not "next_key" in maybe_channels["pagination"]
            or maybe_channels["pagination"]["next_key"] == ""
        ):
            next_key = None

            break

        next_key = maybe_channels["pagination"]["next_key"]

    states = await asyncio.gather(
        *[
            try_multiple_rest_endpoints(
                endpoints[src_chain]["http"],
                f"/ibc/core/channel/v1/channels/{channel['channel_id']}/ports/{channel['port_id']}/client_state",
                session,
            )
            for channel in channels
        ]
    )

    breakpoint()

    if not channels:
        return None

    channels = [
        channel
        for (channel, state) in zip(channels, states)
        if channel["state"] == "STATE_OPEN"
        and state
        and "counterparty" in channel
        and channel["port_id"] == "transfer"
        and len(channel["connection_hops"]) == 1
        and state["identified_client_state"]["client_state"]["chain_id"] == dest_chain
    ]

    if len(channels) == 0:
        return None

    channel = channels[0]
    route_hash = sha256(
        bytes(
            channel["port_id"] + "/" + channel["channel_id"] + "/" + src_denom, "utf-8"
        )
    )
    denom = f"ibc/{route_hash}"

    return DenomChainInfo(
        denom,
        channel["counterparty"]["port_id"],
        channel["counterparty"]["channel_id"],
        dest_chain,
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
