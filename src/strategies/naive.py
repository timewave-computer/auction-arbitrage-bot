from src.contracts.pool.provider import PoolProvider
from src.contracts.auction import AuctionProvider
from src.util import decimal_to_int
from typing import List
import logging

logger = logging.getLogger(__name__)


def strategy(
    pools: dict[str, dict[str, List[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
):
    """
    An arbitrage strategy which identifies price disparities across pools,
    without synthesizing pool routes for denoms with no corresponding pools.
    """

    print(pools)

    for base in auctions.values():
        for pair in base.values():
            # Very naive, simple strategy:
            #
            # Look up reverse pricing information for this pair
            # e.g., if the auction is NTRN USDC, find all providers for USDC NTRN
            # Use the auctiont o purchase NTRN, and find a provider with a higher price
            # in USDC per NTRN than in the auction
            providers = pools.get(pair.asset_b(), {}).get(pair.asset_a(), [])

            if len(providers) == 0:
                continue

            # Purchase price, and amount
            amount = decimal_to_int(pair.remaining_asset_a())
            price = pair.exchange_rate()

            best_return = max(
                map(lambda provider: provider.simulate_swap_asset_b(amount), providers)
            )
            best_exchange_rate = float(best_return) / float(amount)

            # Not worth executing an arbitrage
            if best_exchange_rate <= price:
                continue

            logger.info(
                f"Arbitrage opportunity identified: BUY {amount} {pair.asset_a()} @ {price} {pair.asset_b()} - SELL {amount} {pair.asset_a()} @ {best_exchange_rate} {pair.asset_b()}"
            )
