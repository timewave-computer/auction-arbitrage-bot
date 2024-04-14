from cosmpy.aerial.client import NetworkConfig  # type: ignore
import json
from typing import Any, cast, Optional
import urllib3

"""
Network configuration to be used for all neutron clients.
"""
NEUTRON_NETWORK_CONFIG = NetworkConfig(
    chain_id="neutron-1",
    url="grpc+https://neutron-grpc.publicnode.com:443",
    fee_minimum_gas_price=0.0053,
    fee_denomination="untrn",
    staking_denomination="untrn",
)


def deployments() -> dict[str, Any]:
    with open("contracts/deployments.json") as f:
        return cast(dict[str, Any], json.load(f))


def decimal_to_int(dec: float) -> int:
    return int(dec * 10**18)


def denom_on_chain(src_chain: str, src_denom: str, dest_chain: str) -> Optional[str]:
    """
    Gets a neutron denom's denom on another chain.
    """

    client = urllib3.PoolManager()

    resp = client.request(
        "POST",
        "https://api.skip.money/v1/fungible/assets_from_source",
        headers={"accept": "application/json", "content-type": "application/json"},
        json={
            "allow_multi_tx": False,
            "include_cw20_assets": True,
            "source_asset_denom": src_denom,
            "source_asset_chain_id": src_chain,
            "client_id": "timewave-arb-bot",
        },
    )

    if resp.status != 200:
        return None

    dests = resp.json()["dest_assets"]

    if dest_chain in dests:
        return str(dests[dest_chain]["assets"][0]["denom"])

    return None
