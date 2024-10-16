from sqlite3 import connect
import json
import asyncio
from asyncio import Semaphore
from typing import Any
from src.strategies.util import transfer_raw
from src.scheduler import Ctx, MAX_SKIP_CONCURRENT_CALLS
from src.util import try_multiple_clients
from src.util import custom_neutron_network_config
import aiohttp
from cosmpy.aerial.client import LedgerClient
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.crypto.address import Address


async def main() -> None:
    net_config, denoms = (None, None)

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
            {},
            {},
            Semaphore(MAX_SKIP_CONCURRENT_CALLS),
            connect("test_db.db"),
        )

        await transfer_raw(
            "uosmo",
            "localosmosis-1",
            "uosmo",
            denoms["uosmo"][0]["channel_id"],
            "localneutron-1",
            "osmo1hj5fveer5cjtn4wd6wstzugjfdxzl0xpwhpz63",
            "neutron1hj5fveer5cjtn4wd6wstzugjfdxzl0xpznmsky",
            ctx,
            1,
        )

        balance_after_resp = try_multiple_clients(
            ctx.clients[list(ctx.deployments["pools"]["astroport"].keys())[0]],
            lambda client: client.query_bank_balance(
                Address(
                    ctx.wallet.public_key(),
                    prefix=list(ctx.deployments["pools"]["astroports"].values())[0][
                        "chain_prefix"
                    ],
                ),
                denoms["uosmo"][0]["denom"],
            ),
        )

        assert balance_after_resp
        assert balance_after_resp == 1


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
