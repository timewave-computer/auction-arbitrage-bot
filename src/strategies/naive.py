from src.contracts.pool.provider import PoolProvider
from src.contracts.auction import AuctionProvider
from src.util import decimal_to_int, SYMBOL_DENOMS
from src.scheduler import Ctx
from typing import List, Union
import logging

logger = logging.getLogger(__name__)


def strategy(
    ctx: Ctx,
    pools: dict[str, dict[str, List[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
) -> Ctx:
    """
    An arbitrage strategy which identifies price disparities across pools,
    """

    # Build all possible leg combinations up to n hops starting
    # with the base denom and ending with the base denom
    routes: List[List[Union[PoolProvider, AuctionProvider]]] = []

    base_denom = SYMBOL_DENOMS[ctx.base_symbol]["neutron"]
    base_denom_osmo = SYMBOL_DENOMS[ctx.base_symbol]["osmosis"]

    for pool in [
        *[pool for pool_set in pools[base_denom].values() for pool in pool_set],
        *[pool for pool_set in pools[base_denom_osmo].values() for pool in pool_set],
        *auctions[base_denom].values(),
    ]:
        print(pool)

    return ctx
