# type: ignore

from typing import Any
from src.contracts.route import Leg
from src.strategies.util import collapse_route, build_atomic_arb
from tests.test_scheduler import TEST_WALLET_MNEMONIC
from cosmpy.aerial.wallet import LocalWallet

pytest_plugins = ("pytest_asyncio",)


class MockPool:
    chain_id: str

    def __init__(self, chain_id: str):
        self.chain_id = chain_id

    def swap_msg_asset_a(
        self, wallet: LocalWallet, amount: int, min_amount: int
    ) -> Any:
        return f"swap_a_{amount}"

    def swap_msg_asset_b(
        self, wallet: LocalWallet, amount: int, min_amount: int
    ) -> Any:
        return f"swap_b_{amount}"

    def asset_a(self) -> str:
        return "a"

    def asset_b(self) -> str:
        return "b"


def get_legs_atomic() -> list[MockPool]:
    pool = MockPool(chain_id="neutron-1")

    return [Leg(pool.asset_a, pool.asset_b, pool)] * 3


def get_legs_ibc() -> list[MockPool]:
    return [
        Leg(
            None,
            None,
            MockPool(chain_id="neutron-1"),
        ),
        Leg(
            None,
            None,
            MockPool(chain_id="osmosis-1"),
        ),
        Leg(
            None,
            None,
            MockPool(chain_id="neutron-1"),
        ),
    ]


def test_collapse_route() -> None:
    """
    Tests that an atomic arb will be broken into a single
    contiguous subleg section, and that non-atomic subsequent
    legs will be broken into disjoint sublegs.
    """

    legs_atomic = get_legs_atomic()
    qts = [0] * 3

    collapsed_atomic = collapse_route(zip(legs_atomic, qts))

    assert len(collapsed_atomic) == 1
    assert len(collapsed_atomic[0]) == 3

    for (leg, _), leg_2 in zip(collapsed_atomic[0], legs_atomic):
        assert leg == leg_2

    legs_ntrn_osmo = get_legs_ibc()

    collapsed_disjoint = collapse_route(zip(legs_ntrn_osmo, qts))

    assert len(collapsed_disjoint) == 3
    assert all([len(sublegs) == 1 for sublegs in collapsed_disjoint])


def test_build_atomic_arb() -> None:
    """ "
    Tests that a transaction can be built using a collapsed route.
    """

    legs_atomic = get_legs_atomic()
    qts = [0] * 3

    collapsed_atomic = collapse_route(zip(legs_atomic, qts))

    assert build_atomic_arb(
        collapsed_atomic[0],
        LocalWallet.from_mnemonic(TEST_WALLET_MNEMONIC, prefix="neutron"),
    )
