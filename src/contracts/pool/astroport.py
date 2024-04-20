"""
Implemens contract wrappers for Astroport, providing pricing information for
Astroport pools.
"""

import json
from typing import Any, cast, Optional, List
from dataclasses import dataclass
from cosmpy.aerial.contract import LedgerContract  # type: ignore
from cosmpy.aerial.client import LedgerClient  # type: ignore
from grpc._channel import _InactiveRpcError  # type: ignore
from src.contracts.pool.provider import PoolProvider
from src.util import NEUTRON_NETWORK_CONFIG, WithContract, ContractInfo


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
        contract_info: ContractInfo,
        asset_a: Token | NativeToken,
        asset_b: Token | NativeToken,
    ):
        WithContract.__init__(self, contract_info)
        self.asset_a_denom = asset_a
        self.asset_b_denom = asset_b

    def __exchange_rate(
        self, asset_a: Token | NativeToken, asset_b: Token | NativeToken, amount: int
    ) -> int:
        try:
            simulated_pricing_info = self.contract.query(
                {
                    "simulation": {
                        "offer_asset": {
                            "info": token_to_asset_info(asset_a),
                            "amount": str(amount),
                        },
                        "ask_asset_info": token_to_asset_info(asset_b),
                    }
                }
            )

            return int(simulated_pricing_info["return_amount"])
        except _InactiveRpcError as e:
            # The pool has no assets in it
            if "One of the pools is empty" in e.details():
                return 0

            raise e

    def simulate_swap_asset_a(self, amount: int) -> int:
        return self.__exchange_rate(self.asset_a_denom, self.asset_b_denom, amount)

    def simulate_swap_asset_b(self, amount: int) -> int:
        return self.__exchange_rate(self.asset_b_denom, self.asset_a_denom, amount)

    def asset_a(self) -> str:
        return token_to_addr(self.asset_a_denom)

    def asset_b(self) -> str:
        return token_to_addr(self.asset_b_denom)

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

    def __init__(
        self, deployments: dict[str, Any], poolfile_path: Optional[str] = None
    ):
        self.deployment_info = deployments["pools"]["astroport"]["neutron"]
        self.client = LedgerClient(NEUTRON_NETWORK_CONFIG)
        self.cached_pools = None

        # If the user specifies a pool dump to use, use that
        if poolfile_path is not None:
            with open(poolfile_path, "r", encoding="utf-8") as f:
                poolfile_cts = json.load(f)

                if "pools" in poolfile_cts:
                    self.cached_pools = poolfile_cts["pools"]["neutron_astroport"]

                    return

        deployment_info = self.deployment_info["directory"]
        self.directory_contract = LedgerContract(
            deployment_info["src"], self.client, address=deployment_info["address"]
        )

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
                ContractInfo(
                    self.deployment_info, self.client, poolfile_entry["address"], "pair"
                ),
                asset_a,
                asset_b,
            )

            # Register the pool
            if asset_a_addr not in pools:
                pools[asset_a_addr] = {}

            if asset_b_addr not in pools:
                pools[asset_b_addr] = {}

            pools[asset_a_addr][asset_b_addr] = provider
            pools[asset_b_addr][asset_a_addr] = provider

        return pools

    def pools(self) -> dict[str, dict[str, NeutronAstroportPoolProvider]]:
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

            next_pools = self.directory_contract.query(
                {
                    "pairs": {
                        "start_after": start_after,
                        "limit": 10,
                    }
                }
            )["pairs"]

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
                ContractInfo(
                    self.deployment_info, self.client, pool["contract_addr"], "pair"
                ),
                pair[0],
                pair[1],
            )

            # Register the pool
            if pair_addrs[0] not in asset_pools:
                asset_pools[pair_addrs[0]] = {}

            if pair_addrs[1] not in asset_pools:
                asset_pools[pair_addrs[1]] = {}

            asset_pools[pair_addrs[0]][pair_addrs[1]] = provider
            asset_pools[pair_addrs[1]][pair_addrs[0]] = provider

        return asset_pools

    def contract(self) -> LedgerContract:
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
