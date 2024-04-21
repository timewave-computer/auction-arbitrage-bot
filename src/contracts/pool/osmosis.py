"""
Implements a pool provider for osmosis.
"""

from typing import Any, Optional, List
import urllib3
from src.util import try_multiple_rest_endpoints
from src.contracts.pool.provider import PoolProvider, cached_pools


class OsmosisPoolProvider(PoolProvider):
    """
    Provides pricing and asset information for an arbitrary pair on osmosis.
    """

    def __init__(self, endpoints: list[str], pool_id: int, asset_a: str, asset_b: str):
        """
        Initializes the Osmosis pool provider.
        """

        self.client = urllib3.PoolManager()

        self.endpoints = endpoints
        self.asset_a_denom = asset_a
        self.asset_b_denom = asset_b
        self.pool_id = pool_id

    def __exchange_rate(self, asset_a: str, asset_b: str, amount: int) -> int:
        res = try_multiple_rest_endpoints(
            self.endpoints,
            (
                f"/osmosis/poolmanager/v1beta1/{self.pool_id}"
                f"/estimate/single_pool_swap_exact_amount_in?pool_id={self.pool_id}"
                f"&token_in={amount}{asset_a}&token_out_denom={asset_b}"
            ),
        )

        if not res:
            return 0

        return int(res["token_out_amount"])

    def simulate_swap_asset_a(self, amount: int) -> int:
        return self.__exchange_rate(self.asset_a_denom, self.asset_b_denom, amount)

    def simulate_swap_asset_b(self, amount: int) -> int:
        return self.__exchange_rate(self.asset_b_denom, self.asset_a_denom, amount)

    def asset_a(self) -> str:
        return self.asset_a_denom

    def asset_b(self) -> str:
        return self.asset_b_denom

    def dump(self) -> dict[str, Any]:
        """
        Gets a JSON representation of the pool.
        """

        return {
            "asset_a": self.asset_a(),
            "asset_b": self.asset_b(),
            "pool_id": self.pool_id,
        }


class OsmosisPoolDirectory:
    """
    A wrapper around Osomsis' pool manager providing:
    - Accessors for all pairs on Osmosis
    - OsmosisPoolProviders for each pair
    """

    cached_pools: Optional[list[dict[str, Any]]]

    def __init__(
        self, poolfile_path: Optional[str] = None, endpoints: Optional[list[str]] = None
    ) -> None:
        self.cached_pools = cached_pools(poolfile_path, "osmosis")

        self.client = urllib3.PoolManager()
        self.endpoints = ["https://lcd.osmosis.zone", *(endpoints if endpoints else [])]

    def __pools_cached(self) -> dict[str, dict[str, OsmosisPoolProvider]]:
        """
        Reads the pools in the OsmosisPoolProvider from the contents of the pool file.
        """

        if self.cached_pools is None:
            return {}

        pools: dict[str, dict[str, OsmosisPoolProvider]] = {}

        for poolfile_entry in self.cached_pools:
            asset_a, asset_b = (poolfile_entry["asset_a"], poolfile_entry["asset_b"])
            provider = OsmosisPoolProvider(
                self.endpoints, poolfile_entry["pool_id"], asset_a, asset_b
            )

            # Register the pool
            if asset_a not in pools:
                pools[asset_a] = {}

            if asset_b not in pools:
                pools[asset_b] = {}

            pools[asset_a][asset_b] = provider
            pools[asset_b][asset_a] = provider

        return pools

    def pools(self) -> dict[str, dict[str, OsmosisPoolProvider]]:
        """
        Gets an OsmosisPoolProvider for every pair on Osmosis.
        """

        if self.cached_pools is not None:
            return self.__pools_cached()

        def denoms(pool: dict[str, Any]) -> list[str]:
            if "pool_liquidity" in pool:
                return [asset["denom"] for asset in pool["pool_liquidity"]][:2]

            if "pool_assets" in pool:
                return [asset["token"]["denom"] for asset in pool["pool_assets"]][:2]

            if "token0" in pool:
                return [pool["token0"], pool["token1"]]

            return []

        pools_res = try_multiple_rest_endpoints(
            self.endpoints, "/osmosis/poolmanager/v1beta1/all-pools"
        )

        if not pools_res:
            return {}

        pools = pools_res["pools"]

        # Match each symbol with multiple trading pairs
        asset_pools: dict[str, dict[str, OsmosisPoolProvider]] = {}

        for pool_id, pool in enumerate(pools, 1):
            denom_addrs = denoms(pool)

            if len(denom_addrs) != 2:
                continue

            provider = OsmosisPoolProvider(self.endpoints, pool_id, *denom_addrs)

            # Register the pool
            if denom_addrs[0] not in asset_pools:
                asset_pools[denom_addrs[0]] = {}

            if denom_addrs[1] not in asset_pools:
                asset_pools[denom_addrs[1]] = {}

            asset_pools[denom_addrs[0]][denom_addrs[1]] = provider
            asset_pools[denom_addrs[1]][denom_addrs[0]] = provider

        return asset_pools

    def set_endpoints(self, endpoints: list[str]) -> None:
        """
        Changes the endpoint used by the wrapper to communicate with Osmosis.
        """

        self.endpoints = endpoints

    @staticmethod
    def dump_pools(
        pools: dict[str, dict[str, OsmosisPoolProvider]]
    ) -> List[dict[str, Any]]:
        """
        Constructs a JSON representation of the pools in the OsmosisPoolProvider.
        """

        return list(
            {
                pool.pool_id: pool.dump()
                for base in pools.values()
                for pool in base.values()
            }.values()
        )
