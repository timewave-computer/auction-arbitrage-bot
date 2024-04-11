from src.contracts.pool.provider import PoolProvider
from src.util import NEUTRON_NETWORK_CONFIG
from cosmpy.aerial.contract import LedgerContract
from cosmpy.aerial.client import LedgerClient
from typing import Any, cast, Callable
from functools import cached_property


class Token:
    def __init__(self, addr: str):
        self.contract_addr = addr


class NativeToken:
    def __init__(self, denom: str):
        self.denom = denom


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
    Gets the contract address or denomination (if a native token) of the representation of a Token or NativeToken.
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


class AstroportPoolProvider(PoolProvider):
    """
    Provides pricing and asset information for an arbitrary pair on astroport.
    """

    def __init__(
        self,
        deployment_info: dict[str, Any],
        client: LedgerClient,
        address: str,
        asset_a: Token | NativeToken,
        asset_b: Token | NativeToken,
    ):
        self.deployment_info = deployment_info
        self.client = client
        self.address = address
        self.asset_a_denom = asset_a
        self.asset_b_denom = asset_b

    @cached_property
    def contract(self):
        return LedgerContract(
            self.deployment_info["pair"]["src"],
            self.client,
            address=self.address,
        )

    def __exchange_rate(
        self, asset_a: Token | NativeToken, asset_b: Token | NativeToken, amount: int
    ) -> int:
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
        prev_pool_page = None

        while prev_pool_page is None or len(prev_pool_page) > 0:
            next_pools = self.directory_contract.query(
                {
                    "pairs": {
                        "start_after": prev_pool_page[-1]["asset_infos"]
                        if prev_pool_page is not None
                        else None,
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
                self.deployment_info,
                self.client,
                pool["contract_addr"],
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
