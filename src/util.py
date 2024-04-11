from cosmpy.aerial.client import NetworkConfig
import json
from typing import Any

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
        return json.load(f)
