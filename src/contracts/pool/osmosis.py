import urllib3
import json
from src.contracts.pool.provider import PoolProvider


class OsmosisPoolProvider(PoolProvider):
    """
    Provides pricing and asset information for an arbitrary pair on osmosis.
    """

    def __init__(self, pool_id: int, asset_a: str, asset_b: str):
        self.client = urllib3.PoolManager()
        self.endpoint = "https://lcd.osmosis.zone"
        self.asset_a_denom = asset_a
        self.asset_b_denom = asset_b
        self.pool_id = pool_id

    def __exchange_rate(self, asset_a: str, asset_b: str, amount: int) -> int:
        return int(
            json.loads(
                self.client.request(
                    "GET",
                    f"{self.endpoint}/osmosis/poolmanager/v1beta1/{self.pool_id}/estimate/single_pool_swap_exact_amount_in?pool_id={self.pool_id}&token_in={amount}{asset_a}&token_out_denom={asset_b}",
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


class OsmosisPoolDirectory:
    """
    A wrapper around Osomsis' pool manager providing:
    - Accessors for all pairs on Osmosis
    - OsmosisPoolProviders for each pair
    """

    def __init__(self):
        self.client = urllib3.PoolManager()
        self.endpoint = "https://lcd.osmosis.zone"

    def pools(self) -> dict[str, dict[str, OsmosisPoolProvider]]:
        """
        Gets an OsmosisPoolProvider for every pair on Osmosis.
        """

        def denoms(pool: dict[str, any]) -> list[str]:
            if "pool_liquidity" in pool:
                return [asset["denom"] for asset in pool["pool_liquidity"]][:2]

            if "pool_assets" in pool:
                return [asset["token"]["denom"] for asset in pool["pool_assets"]][:2]

            if "token0" in pool:
                return [pool["token0"], pool["token1"]]

            # TODO: Support cosmwasm pools
            return []

        pools = json.loads(
            self.client.request(
                "GET", f"{self.endpoint}/osmosis/poolmanager/v1beta1/all-pools"
            ).data
        )["pools"]

        # Match each symbol with multiple trading pairs
        asset_pools = {}

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
