from src.contracts.pool.provider import PoolProvider
from src.contracts.auction import AuctionProvider
from src.util import decimal_to_int
from src.scheduler import Ctx
from typing import List
import logging

logger = logging.getLogger(__name__)


def strategy(
    ctx: Ctx,
    pools: dict[str, dict[str, List[PoolProvider]]],
    auctions: dict[str, dict[str, AuctionProvider]],
) -> Ctx:
    """
    An arbitrage strategy which identifies price disparities across pools,
    without synthesizing pool routes for denoms with no corresponding pools.
    """

    return ctx
