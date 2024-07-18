import json
import asyncio
from typing import Any
from src.strategies.util import transfer_raw
from src.contracts.route import Route, Status, Leg
from src.contracts.auction import AuctionProvider
from src.scheduler import Ctx
from src.util import ContractInfo
import aiohttp
from cosmpy.aerial.client import LedgerClient
from cosmpy.aerial.wallet import LocalWallet
from src.util import custom_neutron_network_config


async def main() -> None:
    denom_map, deployments, net_config, denom = (None, None, None, None)

    with open("../../denoms.json", "r", encoding="utf-8") as f:
        denom_map = json.load(f)

    with open("../../net_config.json", "r", encoding="utf-8") as nf:
        net_config = json.load(nf)

    with open("../../denoms.json", "r", encoding="utf-8") as denomf:
        denoms = json.load(denomf)

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
            LocalWallet.from_mnemonic(
                "decorate bright ozone fork gallery riot bus exhaust worth way bone indoor calm squirrel merry zero scheme cotton until shop any excess stage laundry",
                prefix="neutron",
            ),
            {},
            None,
            False,
            session,
            [],
            {},
            denoms,
        )

        await transfer_raw(
            "untrn",
            "localneutron-1",
            "untrn",
            denoms["untrn"][0]["channel_id"],
            "localosmosis-1",
            "neutron1hj5fveer5cjtn4wd6wstzugjfdxzl0xpznmsky",
            "osmo1hj5fveer5cjtn4wd6wstzugjfdxzl0xpwhpz63",
            ctx,
            1,
        )


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())