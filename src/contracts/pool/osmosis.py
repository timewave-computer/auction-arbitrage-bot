"""
Implements a pool provider for osmosis.
"""

from functools import cached_property
from typing import Any, Optional, List
import urllib3
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.aerial.tx import Transaction, SigningCfg
from cosmpy.aerial.client import NetworkConfig, LedgerClient
from cosmpy.crypto.address import Address
from cosmpy.aerial.tx_helpers import SubmittedTx
from osmosis.poolmanager.v1beta1 import tx_pb2, swap_route_pb2
from cosmos.base.v1beta1 import coin_pb2
from src.contracts.pool.provider import PoolProvider, cached_pools
from src.util import (
    try_multiple_rest_endpoints,
    try_multiple_clients_fatal,
)
import aiohttp


class OsmosisPoolProvider(PoolProvider):
    """
    Provides pricing and asset information for an arbitrary pair on osmosis.
    """

    def __init__(
        self,
        endpoints: dict[str, list[str]],
        address: str,
        pool_id: int,
        assets: tuple[str, str],
        session: aiohttp.ClientSession,
    ):
        """
        Initializes the Osmosis pool provider.
        """

        asset_a, asset_b = assets

        self.client = urllib3.PoolManager()
        self.chain_id = "osmosis-1"
        self.chain_prefix = "osmo"
        self.chain_fee_denom = "uosmo"
        self.kind = "osmosis"

        self.endpoints = endpoints["http"]
        self.grpc_endpoints = endpoints["grpc"]
        self.address = address
        self.asset_a_denom = asset_a
        self.asset_b_denom = asset_b
        self.pool_id = pool_id
        self.session = session
        self.swap_fee = 500000

    @cached_property
    def ledgers(self) -> List[LedgerClient]:
        return [
            LedgerClient(
                NetworkConfig(
                    chain_id="osmosis-1",
                    url=url,
                    fee_minimum_gas_price=0.0025,
                    fee_denomination="uosmo",
                    staking_denomination="uosmo",
                )
            )
            for url in self.grpc_endpoints
        ]

    async def __exchange_rate(self, asset_a: str, asset_b: str, amount: int) -> int:
        res = await try_multiple_rest_endpoints(
            self.endpoints,
            (
                f"/osmosis/poolmanager/v1beta1/{self.pool_id}"
                f"/estimate/single_pool_swap_exact_amount_in?pool_id={self.pool_id}"
                f"&token_in={amount}{asset_a}&token_out_denom={asset_b}"
            ),
            self.session,
        )

        if not res or ("code" in res and res["code"] == 13):
            return 0

        return int(res["token_out_amount"])

    async def __rev_exchange_rate(self, asset_a: str, asset_b: str, amount: int) -> int:
        res = await try_multiple_rest_endpoints(
            self.endpoints,
            (
                f"/osmosis/poolmanager/v1beta1/{self.pool_id}"
                f"/estimate/single_pool_swap_exact_amount_out?pool_id={self.pool_id}"
                f"&token_in_denom={asset_b}&token_out={amount}{asset_a}"
            ),
            self.session,
        )

        if not res or ("code" in res and res["code"] == 13):
            return 0

        return int(res["token_in_amount"])

    def __swap(
        self,
        wallet: LocalWallet,
        assets: tuple[str, str],
        amount_min_amount: tuple[int, int],
    ) -> SubmittedTx:
        asset_a, asset_b = assets
        amount, min_token_out_amount = amount_min_amount

        acc = try_multiple_clients_fatal(
            self.ledgers,
            lambda client: client.query_account(
                str(Address(wallet.public_key(), prefix="osmo"))
            ),
        )

        # Perform the swap using the Osmosis pool manager
        tx = Transaction()
        tx.add_message(
            tx_pb2.MsgSwapExactAmountIn(  # pylint: disable=maybe-no-member
                sender=str(Address(wallet.public_key(), prefix="osmo")),
                routes=[
                    swap_route_pb2.SwapAmountInRoute(  # pylint: disable=maybe-no-member
                        pool_id=self.pool_id, token_out_denom=asset_b
                    )
                ],
                token_in=coin_pb2.Coin(  # pylint: disable=maybe-no-member
                    denom=asset_a, amount=str(amount)
                ),
                token_out_min_amount=str(min_token_out_amount),
            )
        )

        gas_limit = 3000000
        gas = try_multiple_clients_fatal(
            self.ledgers,
            lambda client: client.estimate_fee_from_gas(gas_limit),
        )

        tx.seal(SigningCfg.direct(wallet.public_key(), acc.sequence), gas, gas_limit)
        tx.sign(wallet.signer(), self.chain_id, acc.number)
        tx.complete()

        return try_multiple_clients_fatal(
            self.ledgers,
            lambda client: client.broadcast_tx(tx),
        )

    async def __balance(self, asset: str) -> int:
        res = await try_multiple_rest_endpoints(
            self.endpoints,
            (
                f"/osmosis/poolmanager/v1beta1/pools/{self.pool_id}"
                f"/total_pool_liquidity"
            ),
            self.session,
        )

        if (
            not res
            or ("code" in res and res["code"] == 13)
            or len(res["liquidity"]) == 0
        ):
            return 0

        amts = [b for b in res["liquidity"] if b["denom"] == asset]

        if len(amts) == 0:
            return 0

        return int(amts[0]["amount"])

    async def simulate_swap_asset_a(self, amount: int) -> int:
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

    def swap_asset_a(
        self,
        wallet: LocalWallet,
        amount: int,
        min_amount: int,
    ) -> SubmittedTx:  # pylint: disable=duplicate-code
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

    def asset_a(self) -> str:
        return self.asset_a_denom

    def asset_b(self) -> str:
        return self.asset_b_denom

    async def balance_asset_a(self) -> int:
        return await self.__balance(self.asset_a_denom)

    async def balance_asset_b(self) -> int:
        return await self.__balance(self.asset_b_denom)

    def dump(self) -> dict[str, Any]:
        """
        Gets a JSON representation of the pool.
        """

        return {
            "asset_a": self.asset_a(),
            "asset_b": self.asset_b(),
            "pool_id": self.pool_id,
            "address": self.address,
        }

    def __hash__(self) -> int:
        return hash(self.pool_id)


class OsmosisPoolDirectory:
    """
    A wrapper around Osomsis' pool manager providing:
    - Accessors for all pairs on Osmosis
    - OsmosisPoolProviders for each pair
    """

    cached_pools: Optional[list[dict[str, Any]]]

    def __init__(
        self,
        session: aiohttp.ClientSession,
        poolfile_path: Optional[str] = None,
        endpoints: Optional[dict[str, list[str]]] = None,
    ) -> None:
        self.cached_pools = cached_pools(poolfile_path, "osmosis")

        self.client = urllib3.PoolManager()
        self.endpoints = (
            endpoints
            if endpoints
            else {
                "http": ["https://lcd.osmosis.zone"],
                "grpc": ["grpc+https://osmosis-grpc.publicnode.com:443"],
            }
        )
        self.session = session

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
                self.endpoints,
                poolfile_entry["address"],
                poolfile_entry["pool_id"],
                (asset_a, asset_b),
                self.session,
            )

            # Register the pool
            if asset_a not in pools:
                pools[asset_a] = {}

            if asset_b not in pools:
                pools[asset_b] = {}

            pools[asset_a][asset_b] = provider
            pools[asset_b][asset_a] = provider

        return pools

    async def pools(self) -> dict[str, dict[str, OsmosisPoolProvider]]:
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

        pools_res = await try_multiple_rest_endpoints(
            self.endpoints["http"],
            "/osmosis/poolmanager/v1beta1/all-pools",
            self.session,
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

            provider = OsmosisPoolProvider(
                self.endpoints,
                pool["address"],
                pool_id,
                (denom_addrs[0], denom_addrs[1]),
                self.session,
            )

            # Register the pool
            if denom_addrs[0] not in asset_pools:
                asset_pools[denom_addrs[0]] = {}

            if denom_addrs[1] not in asset_pools:
                asset_pools[denom_addrs[1]] = {}

            asset_pools[denom_addrs[0]][denom_addrs[1]] = provider
            asset_pools[denom_addrs[1]][denom_addrs[0]] = provider

        return asset_pools

    def set_endpoints(self, endpoints: dict[str, list[str]]) -> None:
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
