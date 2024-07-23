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

    denom_in: str
    denom_out: str

    port: str
    channel: str


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
    denom_map: Optional[dict[str, list[dict[str, str]]]] = None,
) -> list[list[DenomChainInfo]]:
    """
    Gets a denom's denom and channel on/to other chains.
    """

    if denom_map:
        return [
            [
                DenomChainInfo(
                    denom_info["denom"],
                    denom_info["port_id"],
                    denom_info["channel_id"],
                    denom_info["chain_id"],
                )
            ]
            for denom_info in denom_map.get(src_denom, [])
        ]

    head = {"accept": "application/json", "content-type": "application/json"}

    async with session.post(
        "https://api.skip.money/v1/fungible/assets_from_source",
        headers=head,
        json={
            "allow_multi_tx": False,
            "include_cw20_assets": True,
            "source_asset_denom": src_denom,
            "source_asset_chain_id": src_chain,
            "client_id": "timewave-arb-bot",
        },
    ) as resp:
        if resp.status != 200:
            return []

        dests = (await resp.json())["dest_assets"]

        def chain_info(chain_id: str, info: dict[str, Any]) -> DenomChainInfo:
            info = info["assets"][0]

            if info["trace"] != "":
                parts = info["trace"].split("/")
                port, channel = parts[0], parts[1]

                return DenomChainInfo(
                    denom=info["denom"],
                    port=port,
                    channel=channel,
                    chain_id=chain_id,
                )

            return DenomChainInfo(
                denom=info["denom"], port=None, channel=None, chain_id=chain_id
            )

        return [[chain_info(chain_id, info) for chain_id, info in dests.items()]]


async def denom_info_on_chain(
    src_chain: str,
    src_denom: str,
    dest_chain: str,
    session: aiohttp.ClientSession,
    denom_map: Optional[dict[str, list[dict[str, str]]]] = None,
) -> Optional[list[DenomChainInfo]]:
    """
    Gets a neutron denom's denom and channel on/to another chain.
    """

    if denom_map:
        return [
            DenomChainInfo(
                denom_info["denom"],
                denom_info["port_id"],
                denom_info["channel_id"],
                denom_info["chain_id"],
            )
            for denom_info in denom_map.get(src_denom, [])
            if denom_info["chain_id"] == dest_chain
        ][:1]

    head = {"accept": "application/json", "content-type": "application/json"}

    async with session.post(
        "https://api.skip.money/v1/fungible/assets_from_source",
        headers=head,
        json={
            "allow_multi_tx": False,
            "include_cw20_assets": True,
            "source_asset_denom": src_denom,
            "source_asset_chain_id": src_chain,
            "client_id": "timewave-arb-bot",
        },
    ) as resp:
        if resp.status != 200:
            return None

        dests = (await resp.json())["dest_assets"]

        if dest_chain in dests:
            info = dests[dest_chain]["assets"][0]

            if info["trace"] != "":
                port, channel = info["trace"].split("/")

                return [
                    DenomChainInfo(
                        denom=info["denom"],
                        port=port,
                        channel=channel,
                        chain_id=dest_chain,
                    )
                ]

            return [
                DenomChainInfo(
                    denom=info["denom"], port=None, channel=None, chain_id=dest_chain
                )
            ]

        return None


async def denom_route(
    src_chain: str,
    src_denom: str,
    dest_chain: str,
    dest_denom: str,
    session: aiohttp.ClientSession,
    denom_map: Optional[dict[str, list[dict[str, str]]]] = None,
) -> Optional[list[DenomRouteLeg]]:
    """
    Gets a neutron denom's denom and channel on/to another chain.
    """

    head = {"accept": "application/json", "content-type": "application/json"}

    async with session.post(
        "https://api.skip.money/v2/fungible/route",
        headers=head,
        json={
            "amount_in": "1",
            "source_asset_denom": src_denom,
            "source_asset_chain_id": src_chain,
            "dest_asset_denom": dest_denom,
            "dest_asset_chain_id": dest_chain,
            "allow_multi_tx": True,
            "allow_unsafe": False,
            "bridges": ["IBC"],
        },
    ) as resp:
        if resp.status != 200:
            return None

        ops = (await resp.json())["operations"]

        # The transfer includes a swap or some other operation
        # we can't handle
        if any(("transfer" not in op for op in ops)):
            return None

        transfer_info = ops[0]["transfer"]

        from_chain_info = await chain_info(
            transfer_info["from_chain_id"], session, denom_map
        )
        to_chain_info = await chain_info(
            transfer_info["to_chain_id"], session, denom_map
        )

        if not from_chain_info or not to_chain_info:
            return None

        return [
            DenomRouteLeg(
                src_chain=src_chain,
                dest_chain=dest_chain,
                src_denom=src_denom,
                dest_denom=dest_denom,
                from_chain=from_chain_info,
                to_chain=to_chain_info,
                denom_in=transfer_info["denom_in"],
                denom_out=transfer_info["denom_out"],
                port=transfer_info["port"],
                channel=transfer_info["channel"],
            )
            for op in ops
        ]


async def chain_info(
    chain_id: str,
    session: aiohttp.ClientSession,
    denom_map: Optional[dict[str, list[dict[str, str]]]] = None,
) -> Optional[ChainInfo]:
    """
    Gets basic information about a cosmos chain.
    """

    head = {"accept": "application/json", "content-type": "application/json"}

    async with session.get(
        "https://api.skip.money/v2/info/chains",
        headers=head,
        json={
            "chain_ids": [chain_id],
        },
    ) as resp:
        if resp.status != 200:
            return None

        chains = (await resp.json())["chains"]

        if len(chains) == 0:
            return None

        chain = chains[0]

        return ChainInfo(
            chain_name=chain["chain_name"],
            chain_id=chain["chain_id"],
            pfm_enabled=chain["pfm_enabled"],
            supports_memo=chain["supports_memo"],
            bech32_prefix=chain["bech32_prefix"],
            fee_asset=chain["fee_assets"][0]["denom"],
            chain_type=chain["chain_type"],
            pretty_name=chain["pretty_name"],
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
