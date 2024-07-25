# type: ignore

from dataclasses import dataclass
from src.contracts.route import Leg
from src.strategies.util import collapse_route
import pytest

pytest_plugins = ("pytest_asyncio",)


@dataclass
class MockPool:
    chain_id: str


@pytest.mark.asyncio
async def test_collapse_route() -> None:
    """
    Tests that an atomic arb will be broken into a single
    contiguous subleg section, and that non-atomic subsequent
    legs will be broken into disjoint sublegs.
    """

    legs_atomic = [
        Leg(
            None,
            None,
            MockPool(chain_id="neutron-1"),
        )
    ] * 3
    qts = [0] * 3

    collapsed_atomic = collapse_route(zip(legs_atomic, qts))

    assert len(collapsed_atomic) == 1
    assert len(collapsed_atomic[0]) == 3

    for (leg, _), leg_2 in zip(collapsed_atomic[0], legs_atomic):
        assert leg == leg_2

    legs_ntrn_osmo = [
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

    collapsed_disjoint = collapse_route(zip(legs_ntrn_osmo, qts))

    assert len(collapsed_disjoint) == 3
    assert all([len(sublegs) == 1 for sublegs in collapsed_disjoint])
