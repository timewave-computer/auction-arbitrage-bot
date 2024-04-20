"""
Implements a pool provider for osmosis.
"""

from typing import Any, Optional, List
import json
import urllib3
from src.contracts.pool.provider import PoolProvider


class OsmosisPoolProvider(PoolProvider):
    """
    Provides pricing and asset information for an arbitrary pair on osmosis.
    """

    def __init__(self, pool_id: int, asset_a: str, asset_b: str):
        """
        Initializes the Osmosis pool provider.
        """

        self.client = urllib3.PoolManager()

        # TODO: Allow multiple RPC's
        self.endpoint = "https://lcd.osmosis.zone"
        self.asset_a_denom = asset_a
        self.asset_b_denom = asset_b
        self.pool_id = pool_id

    def __exchange_rate(self, asset_a: str, asset_b: str, amount: int) -> int:
        return int(
            json.loads(
                self.client.request(
                    "GET",
                    (
                        f"{self.endpoint}/osmosis/poolmanager/v1beta1/{self.pool_id}"
                        f"/estimate/single_pool_swap_exact_amount_in?pool_id={self.pool_id}"
                        f"&token_in={amount}{asset_a}&token_out_denom={asset_b}"
                    ),
                ).data
            )["token_out_amount"]
        )

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

    def __init__(self, poolfile_path: Optional[str] = None) -> None:
        self.cached_pools = None

        # If the user specifies a pool dump to use, use that
        if poolfile_path is not None:
            with open(poolfile_path, "r", encoding="utf-8") as f:
                poolfile_cts = json.load(f)

                if "pools" in poolfile_cts:
                    self.cached_pools = poolfile_cts["pools"]["osmosis"]

                    return

        self.client = urllib3.PoolManager()
        self.endpoint = "https://lcd.osmosis.zone"

    def __pools_cached(self) -> dict[str, dict[str, OsmosisPoolProvider]]:
        """
        Reads the pools in the OsmosisPoolProvider from the contents of the pool file.
        """

        if self.cached_pools is None:
            return {}

        pools: dict[str, dict[str, OsmosisPoolProvider]] = {}

        for poolfile_entry in self.cached_pools:
            asset_a, asset_b = (poolfile_entry["asset_a"], poolfile_entry["asset_b"])
            provider = OsmosisPoolProvider(poolfile_entry["pool_id"], asset_a, asset_b)

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

        pools = json.loads(
            self.client.request(
                "GET", f"{self.endpoint}/osmosis/poolmanager/v1beta1/all-pools"
            ).data
        )["pools"]

        # Match each symbol with multiple trading pairs
        asset_pools: dict[str, dict[str, OsmosisPoolProvider]] = {}

        for pool_id, pool in enumerate(pools, 1):
            denom_addrs = denoms(pool)

            if len(denom_addrs) != 2:
                continue

            provider = OsmosisPoolProvider(pool_id, *denom_addrs)

            # Register the pool
            if denom_addrs[0] not in asset_pools:
                asset_pools[denom_addrs[0]] = {}

            if denom_addrs[1] not in asset_pools:
                asset_pools[denom_addrs[1]] = {}

            asset_pools[denom_addrs[0]][denom_addrs[1]] = provider
            asset_pools[denom_addrs[1]][denom_addrs[0]] = provider

        return asset_pools

    def set_endpoint(self, endpoint: str) -> None:
        """
        Changes the endpoint used by the wrapper to communicate with Osmosis.
        """

        self.endpoint = endpoint

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
