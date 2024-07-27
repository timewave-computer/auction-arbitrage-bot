# type: ignore

import json
from typing import Any
from src.scheduler import Ctx
from src.contracts.route import Leg
from src.strategies.util import collapse_route, build_atomic_arb, transfer_raw
from src.util import denom_info_on_chain, denom_route, custom_neutron_network_config
from tests.test_scheduler import TEST_WALLET_MNEMONIC
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.aerial.client import LedgerClient
from cosmpy.crypto.address import Address
import pytest
import aiohttp

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


@pytest.mark.asyncio
async def test_transfer_raw() -> None:
    """
    Transfers a tiny amount of USDC to osmosis.
    """

    net_config = None

    with open("net_conf.json", "r", encoding="utf-8") as nf:
        net_config = json.load(nf)

    wallet = LocalWallet.from_mnemonic(TEST_WALLET_MNEMONIC, prefix="neutron")

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(force_close=True, limit_per_host=1),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        ctx: Ctx[Any] = Ctx(
            {
                chain_id: [
                    LedgerClient(
                        custom_neutron_network_config(endpoint, chain_id=chain_id)
                    )
                    for endpoint in endpoints["grpc"]
                ]
                for chain_id, endpoints in net_config.items()
            },
            net_config,
            wallet,
            {},
            None,
            False,
            session,
            [],
            {},
            {},
        )

        denom_infos_on_dest = await denom_info_on_chain(
            "neutron-1",
            "ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81",
            "osmosis-1",
            session,
        )

        if not denom_infos_on_dest:
            raise ValueError(
                f"Missing denom info for transfer {denom} (neutron-1) -> osmosis-1"
            )

        ibc_route = await denom_route(
            "neutron-1",
            "ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81",
            "osmosis-1",
            denom_infos_on_dest[0].denom,
            session,
        )

        if not ibc_route or len(ibc_route) == 0:
            raise ValueError(f"No route from {denom} to {leg.backend.chain_id}")

        src_channel_id = ibc_route[0].channel
        sender_addr = str(
            Address(wallet.public_key(), prefix=ibc_route[0].from_chain.bech32_prefix)
        )
        receiver_addr = str(
            Address(wallet.public_key(), prefix=ibc_route[0].to_chain.bech32_prefix)
        )

        memo: Optional[str] = None

        for ibc_leg in reversed(ibc_route[1:]):
            memo = json.dumps(
                {
                    "forward": {
                        "receiver": "pfm",
                        "port": ibc_leg.port,
                        "channel": ibc_leg.channel,
                        "timeout": "10m",
                        "retries": 2,
                        "next": memo,
                    }
                }
            )

        await transfer_raw(
            "ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81",
            ibc_route[0].from_chain.chain_id,
            "untrn",
            src_channel_id,
            ibc_route[0].to_chain.chain_id,
            sender_addr,
            receiver_addr,
            ctx,
            1,
            memo=memo,
        )
