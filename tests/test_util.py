"""
Tests that the skip util methods work as expected.
"""

from src.util import denom_info, denom_info_on_chain, denom_route
import pytest
import aiohttp

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.asyncio
async def test_denom_info() -> None:
    """ "
    Tests that skip can fetch the destination chains for untrn.
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(force_close=True, limit_per_host=1),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        info = await denom_info("neutron-1", "untrn", session)

        assert info
        assert len(info) > 0
        assert len(info[0]) > 0

        assert info[0][0].chain_id == "archway-1"
        assert (
            info[0][0].denom
            == "ibc/9E3CDA65E02637E219B43802452D6B37D782F466CF76ECB9F47A2E00C07C4769"
        )
        assert info[0][0].port == "transfer"
        assert info[0][0].channel == "channel-61"


@pytest.mark.asyncio
async def test_denom_info_on_chain() -> None:
    """ "
    Tests that skip can fetch the osmosis destination chain info for untrn.
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(force_close=True, limit_per_host=1),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        info = await denom_info_on_chain("neutron-1", "untrn", "osmosis-1", session)

        assert info
        assert len(info) > 0

        assert (
            info[0].denom
            == "ibc/126DA09104B71B164883842B769C0E9EC1486C0887D27A9999E395C2C8FB5682"
        )
        assert info[0].port == "transfer"
        assert info[0].channel == "channel-874"
        assert info[0].chain_id == "osmosis-1"


@pytest.mark.asyncio
async def test_denom_route() -> None:
    """ "
    Tests that skip can fetch the route for USDC from neutron to osmosis.
    """

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(force_close=True, limit_per_host=1),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        info = await denom_route(
            "neutron-1",
            "ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81",
            "osmosis-1",
            "ibc/498A0751C798A0D9A389AA3691123DADA57DAA4FE165D5C75894505B876BA6E4",
            session,
        )

        assert info
        assert len(info) > 0

        assert info
