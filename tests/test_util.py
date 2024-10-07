"""
Tests that the skip util methods work as expected.
"""

from src.util import DenomRouteQuery
from tests.util import ctx
import pytest

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.asyncio
async def test_denom_info() -> None:
    """ "
    Tests that skip can fetch the destination chains for untrn.
    """

    async with ctx() as test_ctx:
        info = await test_ctx.query_denom_info("neutron-1", "untrn")

        assert info
        assert len(info) > 0

        assert info[0].dest_chain_id == "archway-1"
        assert (
            info[0].denom
            == "ibc/9E3CDA65E02637E219B43802452D6B37D782F466CF76ECB9F47A2E00C07C4769"
        )


@pytest.mark.asyncio
async def test_denom_info_on_chain() -> None:
    """ "
    Tests that skip can fetch the osmosis destination chain info for untrn.
    """

    async with ctx() as test_ctx:
        info = await test_ctx.query_denom_info_on_chain(
            "neutron-1", "untrn", "osmosis-1"
        )

        assert info

        assert (
            info.denom
            == "ibc/126DA09104B71B164883842B769C0E9EC1486C0887D27A9999E395C2C8FB5682"
        )
        assert info.dest_chain_id == "osmosis-1"

        info = await test_ctx.query_denom_info_on_chain(
            "neutron-1",
            "ibc/376222D6D9DAE23092E29740E56B758580935A6D77C24C2ABD57A6A78A1F3955",
            "osmosis-1",
        )

        assert info

        assert info.denom == "uosmo"
        assert info.dest_chain_id == "osmosis-1"


@pytest.mark.asyncio
async def test_denom_route() -> None:
    """ "
    Tests that skip can fetch the route for USDC from neutron to osmosis.
    """

    async with ctx() as test_ctx:

        info = await test_ctx.query_denom_route(
            DenomRouteQuery(
                src_chain="neutron-1",
                src_denom="ibc/376222D6D9DAE23092E29740E56B758580935A6D77C24C2ABD57A6A78A1F3955",
                dest_chain="osmosis-1",
                dest_denom="uosmo",
            )
        )

        assert info
        assert len(info) > 0

        assert info

        info = await test_ctx.query_denom_route(
            DenomRouteQuery(
                src_chain="neutron-1",
                src_denom="ibc/376222D6D9DAE23092E29740E56B758580935A6D77C24C2ABD57A6A78A1F3955",
                dest_chain="osmosis-1",
                dest_denom="uosmo",
            )
        )

        assert info
        assert len(info) == 1

        assert info[0].to_chain.chain_id == "osmosis-1"
