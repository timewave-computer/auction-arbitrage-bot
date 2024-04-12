from cosmpy.aerial.client import NetworkConfig  # type: ignore
import json
from typing import Any, cast, Optional
import sys
import logging

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

"""
IBC denoms for different token symbols on different chains.
"""
SYMBOL_DENOMS = {
    "ATOM": {
        "neutron": "ibc/C4CFF46FD6DE35CA4CF4CE031E643C8FDC9BA4B99AE598E9B0ED98FE3A2319F9",
        "osmosis": "ibc/27394FB092D2ECCD56123C74F36E4C1F926001CEADA9CA97EA622B25F41E5EB2",
    },
    "TIA": {
        "neutron": "ibc/773B4D0A3CD667B2275D5A4A7A2F0909C0BA0F4059C0B9181E680DDF4965DCC7",
        "osmosis": "ibc/D79E7D83AB399BFFF93433E54FAA480C191248FC556924A2A8351AE2638B3877",
    },
    "stTIA": {
        "neutron": "ibc/6569E05DEE32B339D9286A52BE33DFCEFC97267F23EF9CFDE0C055140967A9A5",
        "osmosis": "ibc/698350B8A61D575025F3ED13E9AC9C0F45C89DEFE92F76D5838F1D3C1A7FF7C9",
    },
    "AXL": {
        "neutron": "ibc/C0E66D1C81D8AAF0E6896E05190FDFBC222367148F86AC3EA679C28327A763CD",
        "osmosis": "ibc/903A61A498756EA560B85A85132D3AEE21B5DEDD41213725D22ABF276EA6945E",
    },
    "axlUSDC": {
        "neutron": "ibc/F082B65C88E4B6D5EF1DB243CDA1D331D002759E938A0F5CD3FFDC5D53B3E349",
        "osmosis": "ibc/D189335C6E4A68B513C10AB227BF1C1D38C746766278BA3EEB4FB14124F1D858",
    },
    "axlWETH": {
        "neutron": "ibc/A585C2D15DCD3B010849B453A2CFCB5E213208A5AB665691792684C26274304D"
    },
    "USDC": {
        "neutron": "ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81",
        "osmosis": "ibc/498A0751C798A0D9A389AA3691123DADA57DAA4FE165D5C75894505B876BA6E4",
    },
    "DYDX": {
        "neutron": "ibc/2CB87BCE0937B1D1DFCEE79BE4501AAF3C265E923509AEAC410AD85D27F35130",
        "osmosis": "ibc/831F0B1BBB1D08A2B75311892876D71565478C532967545476DF4C2D7492E48C",
    },
    "OSMO": {
        "neutron": "ibc/376222D6D9DAE23092E29740E56B758580935A6D77C24C2ABD57A6A78A1F3955",
        "osmosis": "uosmo",
    },
    "NLS": {
        "neutron": "ibc/6C9E6701AC217C0FC7D74B0F7A6265B9B4E3C3CDA6E80AADE5F950A8F52F9972",
        "osmosis": "ibc/D9AFCECDD361D38302AA66EB3BAC23B95234832C51D12489DC451FA2B7C72782",
    },
}


def deployments() -> dict[str, Any]:
    with open("contracts/deployments.json") as f:
        return cast(dict[str, Any], json.load(f))


def decimal_to_int(dec: float) -> int:
    return int(dec * 10**18)
