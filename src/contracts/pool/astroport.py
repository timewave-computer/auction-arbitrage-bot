# pylint: disable=duplicate-code
"""
Implemens contract wrappers for Astroport, providing pricing information for
Astroport pools.
"""

from functools import cached_property
from typing import Any, cast, Optional, List
from dataclasses import dataclass
from cosmpy.aerial.contract import LedgerContract
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.aerial.tx_helpers import SubmittedTx
from cosmpy.aerial.client import LedgerClient
from grpc._channel import _InactiveRpcError
from src.contracts.pool.provider import PoolProvider, cached_pools
from src.util import (
    WithContract,
    ContractInfo,
    try_query_multiple,
    try_exec_multiple_fatal,
    custom_neutron_network_config,
)
import aiohttp
import grpc

MAX_SPREAD = "0.05"


@dataclass
class Token:
    """
    A token returned by an Astroport contract
    """

    contract_addr: str


@dataclass
class NativeToken:
    """
    A NativeToken returned by an Astroport contract
    """

    denom: str


def asset_info_to_token(info: dict[str, Any]) -> NativeToken | Token:
    """
    Converts an Astroport Pairs {} query message response list member to a
    representation as Token or NativeToken.
    """

    if "token" in info:
        return Token(cast(dict[str, Any], info["token"])["contract_addr"])

    return NativeToken(cast(dict[str, Any], info["native_token"])["denom"])


def token_to_addr(token: NativeToken | Token) -> str:
    """
    Gets the contract address or denomination (if a native token) of
    the representation of a Token or NativeToken.
    """

    if isinstance(token, NativeToken):
        return token.denom

    return token.contract_addr


def token_to_asset_info(token: NativeToken | Token) -> dict[str, Any]:
    """
    Gets the JSON astroport AssetInfo representation of a token representation.
    """

    if isinstance(token, NativeToken):
        return {"native_token": {"denom": token.denom}}

    return {"token": {"contract_addr": token.contract_addr}}


class NeutronAstroportPoolProvider(PoolProvider, WithContract):
    """
    Provides pricing and asset information for an arbitrary pair on astroport.
    """

    def __init__(
        self,
        endpoints: dict[str, list[str]],
        contract_info: ContractInfo,
        asset_a: Token | NativeToken,
        asset_b: Token | NativeToken,
        session: aiohttp.ClientSession,
        grpc_channels: list[grpc.aio.Channel],
    ):
        WithContract.__init__(self, contract_info)
        self.asset_a_denom = asset_a
        self.asset_b_denom = asset_b
        self.chain_id = contract_info.clients[0].query_chain_id()
        self.chain_prefix = "neutron"
        self.chain_fee_denom = "untrn"
        self.kind = "astroport"
        self.endpoints = endpoints["http"]
        self.session = session
        self.grpc_channels = grpc_channels
        self.swap_fee = 50000

    async def __exchange_rate(
        self, asset_a: Token | NativeToken, asset_b: Token | NativeToken, amount: int
    ) -> int:
        try:
            simulated_pricing_info = await try_query_multiple(
                self.contracts,
                {
                    "simulation": {
                        "offer_asset": {
                            "info": token_to_asset_info(asset_a),
                            "amount": str(amount),
                        },
                        "ask_asset_info": token_to_asset_info(asset_b),
                    }
                },
                self.session,
                self.grpc_channels,
            )

            if not simulated_pricing_info:
                return 0

            return int(simulated_pricing_info["return_amount"])
        except _InactiveRpcError as e:
            details = e.details()

            # The pool has no assets in it
            if details is not None and "One of the pools is empty" in details:
                return 0

            raise e

    async def __rev_exchange_rate(
        self, asset_a: Token | NativeToken, asset_b: Token | NativeToken, amount: int
    ) -> int:
        try:
            simulated_pricing_info = await try_query_multiple(
                self.contracts,
                {
                    "reverse_simulation": {
                        "offer_asset_info": token_to_asset_info(asset_b),
                        "ask_asset": {
                            "info": token_to_asset_info(asset_a),
                            "amount": str(amount),
                        },
                    }
                },
                self.session,
                self.grpc_channels,
            )

            if not simulated_pricing_info:
                return 0

            return int(simulated_pricing_info["offer_amount"])
        except (_InactiveRpcError, grpc.aio._call.AioRpcError) as e:
            details = e.details()

            # The pool has no assets in it
            if details is not None and "One of the pools is empty" in details:
                return 0

            raise e

    def __swap(
        self,
        wallet: LocalWallet,
        assets: tuple[Token | NativeToken, Token | NativeToken],
        amount_min_amount: tuple[int, int],
    ) -> SubmittedTx:
        asset_a, asset_b = assets
        amount, min_amount = amount_min_amount

        return try_exec_multiple_fatal(
            self.contracts,
            wallet,
            {
                "swap": {
                    "offer_asset": {
                        "info": token_to_asset_info(asset_a),
                        "amount": str(amount),
                    },
                    "ask_asset_info": token_to_asset_info(asset_b),
                    "max_spread": MAX_SPREAD,
                }
            },
            funds=f"{amount}{token_to_addr(asset_a)}",
            gas_limit=3000000,
        )

    async def __balance(self, asset: Token | NativeToken) -> int:
        resp = await try_query_multiple(
            self.contracts, {"pool": {}}, self.session, self.grpc_channels
        )

        if not resp:
            return 0

        balances = resp["assets"]

        balance = next(b for b in balances if b["info"] == token_to_asset_info(asset))

        if not balance:
            return 0

        return int(balance["amount"])

    def swap_asset_a(
        self, wallet: LocalWallet, amount: int, min_amount: int
    ) -> SubmittedTx:
        return self.__swap(
            wallet,
            (self.asset_a_denom, self.asset_b_denom),
            (amount, min_amount),
        )

    def swap_asset_b(
        self,
        wallet: LocalWallet,
        amount: int,
        min_amount: int,
    ) -> SubmittedTx:
        return self.__swap(
            wallet,
            (self.asset_b_denom, self.asset_a_denom),
            (amount, min_amount),
        )

    async def simulate_swap_asset_a(
        self,
        amount: int,
    ) -> int:
        return await self.__exchange_rate(
            self.asset_a_denom, self.asset_b_denom, amount
        )

    async def simulate_swap_asset_b(self, amount: int) -> int:
        return await self.__exchange_rate(
            self.asset_b_denom, self.asset_a_denom, amount
        )

    async def reverse_simulate_swap_asset_a(self, amount: int) -> int:
        return await self.__rev_exchange_rate(
            self.asset_a_denom, self.asset_b_denom, amount
        )

    async def reverse_simulate_swap_asset_b(self, amount: int) -> int:
        return await self.__rev_exchange_rate(
            self.asset_b_denom, self.asset_a_denom, amount
        )

    def asset_a(self) -> str:
        return token_to_addr(self.asset_a_denom)

    def asset_b(self) -> str:
        return token_to_addr(self.asset_b_denom)

    async def balance_asset_a(self) -> int:
        return await self.__balance(self.asset_a_denom)

    async def balance_asset_b(self) -> int:
        return await self.__balance(self.asset_b_denom)

    def dump(self) -> dict[str, Any]:
        """
        Gets a JSON representation of the pool.
        """

        return {
            "asset_a": token_to_asset_info(self.asset_a_denom),
            "asset_b": token_to_asset_info(self.asset_b_denom),
            "address": self.contract_info.address,
        }

    def __hash__(self) -> int:
        return hash(self.contract_info.address)


class NeutronAstroportPoolDirectory:
    """
    A wrapper around Astroport's factory providing:
    - Accessors for all pairs on Astroport
    - NeutronAstroportPoolProviders for each pair
    """

    cached_pools: Optional[list[dict[str, Any]]]
    session: aiohttp.ClientSession

    def __init__(
        self,
        deployments: dict[str, Any],
        session: aiohttp.ClientSession,
        grpc_channels: list[grpc.aio.Channel],
        poolfile_path: Optional[str] = None,
        endpoints: Optional[dict[str, list[str]]] = None,
    ):
        self.deployment_info = deployments["pools"]["astroport"]["neutron"]
        self.cached_pools = cached_pools(poolfile_path, "neutron_astroport")
        self.session = session
        self.grpc_channels = grpc_channels
        self.endpoints = (
            endpoints
            if endpoints
            else {
                "http": ["https://neutron-rest.publicnode.com"],
                "grpc": ["grpc+https://neutron-grpc.publicnode.com:443"],
            }
        )

        deployment_info = self.deployment_info["directory"]
        self.directory_contract = [
            LedgerContract(
                deployment_info["src"], client, address=deployment_info["address"]
            )
            for client in self.clients
        ]

    @cached_property
    def clients(self) -> List[LedgerClient]:
        return [
            LedgerClient(custom_neutron_network_config(endpoint))
            for endpoint in self.endpoints["grpc"]
        ]

    def __pools_cached(self) -> dict[str, dict[str, NeutronAstroportPoolProvider]]:
        """
        Reads the pools in the AstroportPoolProvider from the contents of the pool file.
        """

        if self.cached_pools is None:
            return {}

        pools: dict[str, dict[str, NeutronAstroportPoolProvider]] = {}

        for poolfile_entry in self.cached_pools:
            asset_a, asset_b = (
                asset_info_to_token(poolfile_entry["asset_a"]),
                asset_info_to_token(poolfile_entry["asset_b"]),
            )
            asset_a_addr, asset_b_addr = (
                token_to_addr(asset_a),
                token_to_addr(asset_b),
            )
            provider = NeutronAstroportPoolProvider(
                self.endpoints,
                ContractInfo(
                    self.deployment_info,
                    self.clients,
                    poolfile_entry["address"],
                    "pair",
                ),
                asset_a,
                asset_b,
                self.session,
                self.grpc_channels,
            )

            # Register the pool
            if asset_a_addr not in pools:
                pools[asset_a_addr] = {}

            if asset_b_addr not in pools:
                pools[asset_b_addr] = {}

            pools[asset_a_addr][asset_b_addr] = provider
            pools[asset_b_addr][asset_a_addr] = provider

        return pools

    async def pools(self) -> dict[str, dict[str, NeutronAstroportPoolProvider]]:
        """
        Gets an NeutronAstroportPoolProvider for every pair on Astroport.
        """

        if self.cached_pools is not None:
            return self.__pools_cached()

        # Load all pools in 10-pool batches
        pools = []
        prev_pool_page: Optional[List[dict[str, Any]]] = None

        while prev_pool_page is None or len(prev_pool_page) > 0:
            start_after = None

            if prev_pool_page is not None:
                start_after = prev_pool_page[-1]["asset_infos"]

            maybe_next_pools = await try_query_multiple(
                self.directory_contract,
                {
                    "pairs": {
                        "start_after": start_after,
                        "limit": 10,
                    }
                },
                self.session,
                self.grpc_channels,
            )

            if not maybe_next_pools:
                break

            next_pools = maybe_next_pools["pairs"]

            pools.extend(next_pools)
            prev_pool_page = next_pools

        # All denom symbols and token contract addresses
        asset_pools: dict[str, dict[str, NeutronAstroportPoolProvider]] = {}

        # Pool wrappers for each asset
        for pool in pools:
            pair = [asset_info_to_token(asset) for asset in pool["asset_infos"]]
            pair_addrs = [token_to_addr(asset) for asset in pair]

            # Check for malformed denom addrs
            if "<" in pair_addrs[0] or "<" in pair_addrs[1]:
                continue

            provider = NeutronAstroportPoolProvider(
                self.endpoints,
                ContractInfo(
                    self.deployment_info,
                    self.clients,
                    pool["contract_addr"],
                    "pair",
                ),
                pair[0],
                pair[1],
                self.session,
                self.grpc_channels,
            )

            # Register the pool
            if pair_addrs[0] not in asset_pools:
                asset_pools[pair_addrs[0]] = {}

            if pair_addrs[1] not in asset_pools:
                asset_pools[pair_addrs[1]] = {}

            asset_pools[pair_addrs[0]][pair_addrs[1]] = provider
            asset_pools[pair_addrs[1]][pair_addrs[0]] = provider

        return asset_pools

    def contract(self) -> list[LedgerContract]:
        """
        Gets the contract backing the pool provider.
        """

        return self.directory_contract

    @staticmethod
    def dump_pools(
        pools: dict[str, dict[str, NeutronAstroportPoolProvider]]
    ) -> List[dict[str, Any]]:
        """
        Constructs a JSON representation of the pools in the AstroportPoolProvider.
        """

        return list(
            {
                pool.contract_info.address: pool.dump()
                for base in pools.values()
                for pool in base.values()
            }.values()
        )
