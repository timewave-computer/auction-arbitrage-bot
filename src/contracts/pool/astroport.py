"""
Implemens contract wrappers for Astroport, providing pricing information for
Astroport pools.
"""

from typing import Any, cast, Optional, List
from dataclasses import dataclass
from cosmpy.aerial.contract import LedgerContract  # type: ignore
from cosmpy.aerial.client import LedgerClient  # type: ignore
from grpc._channel import _InactiveRpcError  # type: ignore
from src.contracts.pool.provider import PoolProvider
from src.util import NEUTRON_NETWORK_CONFIG, WithContract


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


class AstroportPoolProvider(PoolProvider, WithContract):
    """
    Provides pricing and asset information for an arbitrary pair on astroport.
    """

    def __init__(
        self,
        contract_info: tuple[dict[str, Any], LedgerClient, str],
        asset_a: Token | NativeToken,
        asset_b: Token | NativeToken,
    ):
        WithContract.__init__(
            self, contract_info[0], contract_info[1], contract_info[2], "pair"
        )
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


class AstroportPoolDirectory:
    """
    A wrapper around Astroport's factory providing:
    - Accessors for all pairs on Astroport
    - AstroportPoolProviders for each pair
    """

    def __init__(self, deployments: dict[str, Any]):
        self.client = LedgerClient(NEUTRON_NETWORK_CONFIG)
        self.deployment_info = deployments["pools"]["astroport"]["neutron"]

        deployment_info = self.deployment_info["directory"]
        self.directory_contract = LedgerContract(
            deployment_info["src"], self.client, address=deployment_info["address"]
        )

    def pools(self) -> dict[str, dict[str, AstroportPoolProvider]]:
        """
        Gets an AstroportPoolProvider for every pair on Astroport.
        """

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
        asset_pools: dict[str, dict[str, AstroportPoolProvider]] = {}

        # Pool wrappers for each asset
        for pool in pools:
            pair = [asset_info_to_token(asset) for asset in pool["asset_infos"]]
            pair_addrs = [token_to_addr(asset) for asset in pair]

            # Check for malformed denom addrs
            if "<" in pair_addrs[0] or "<" in pair_addrs[1]:
                continue

            provider = AstroportPoolProvider(
                (self.deployment_info, self.client, pool["contract_addr"]),
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
