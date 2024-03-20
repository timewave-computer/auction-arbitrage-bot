from src.contracts.pools import PoolProvider
from cosmpy.aerial.contract import LedgerContract
from cosmpy.aerial.client import LedgerClient, NetworkConfig


class Token:
    def __init__(addr: str):
        self.contract_addr = addr

    def contract_addr(self) -> str:
        return self.contract_addr


class NativeToken:
    def __init__(denom: str):
        self.denom = denom

    def denom(self) -> str:
        return self.denom


def asset_info_to_token(info: dict[str, Any]) -> NativeToken | Token:
    """
    Converts an Astroport Pairs {} query message response list member to a
    representation as Token or NativeToken.
    """

    if "contract_addr" in info:
        return Token(info["contract_addr"])

    return NativeToken(info["denom"])


def token_to_addr(token: NativeToken | Token) -> str:
    """
    Gets the contract address or denomination (if a native token) of the representation of a Token or NativeToken.
    """

    if isinstance(token, NativeToken):
        return token.denom()

    return token.contract_addr()


def token_to_asset_info(token: NativeToken | Token) -> dict[str, Any]:
    """
    Gets the JSON astroport AssetInfo representation of a token representation.
    """

    if isinstance(token, NativeToken):
        return {"native_token": {"denom": token.denom()}}

    return {"token": {"contract_addr": token.contract_addr()}}


class AstroportPoolDirectory:
    """
    A wrapper around Astroport's factory providing:
    - Accessors for all pairs on Astroport
    - AstroportPoolProviders for each pair
    """

    def __init__(deployments: dict[str, Any]):
        self.client = LedgerClient(
            NetworkConfig(
                chain_id="neutron-1",
                url="https://neutron-rpc.publicnode.com:443",
                fee_minimum_gas_price=0.0053,
                fee_denomination="untrn",
                staking_denomination="untrn",
            )
        )
        self.deployment_info = deployments["pools"]["astroport"]["neutron"]

        deployment_info = self.deployment_info["directory"]
        self.directory_contract = LedgerContract(
            deployment_info["src"], client, address=deployment_info["address"]
        )

    def pairs(self) -> List[List[Token | NativeToken]]:
        """
        Gets a list of pairs on Astroport.
        """

        return [
            [asset_info_to_token(pair) for asset_info in pair["asset_infos"]]
            for pair in self.directory_contract.query(
                {"pairs": {"start_after": None, "limit": None}}
            )
        ]

    def pools(self) -> List[AstroportPoolProvider]:
        """
        Gets an AstroportPoolProvider for every pair on Astroport.
        """

        def contract_addr(assets: List[Token | NativeToken]) -> str:
            return self.directory_contract.query(
                {
                    "pair": {
                        "asset_infos": [token_to_asset_info(asset) for asset in assets]
                    }
                }
            )["contract_addr"]

        contract_addrs = {pair: contract_addr(pair) for pair in self.pairs()}

        return [
            AstroportPoolProvider(
                LedgerContract(
                    self.deployment_info["pair"], self.client, address=pair[1]
                ),
                pair[0][0],
                pair[0][1],
            )
            for pair in contract_addrs.items()
        ]


class AstroportPoolProvider(PoolProvider):
    """
    Provides pricing and asset information for an arbitrary pair on astroport.
    """

    def __init__(
        contract: LedgerContract,
        asset_a: Token | NativeToken,
        asset_b: Token | NativeToken,
    ):
        self.contract = contract
        self.asset_a = asset_a
        self.asset_b = asset_b

    def __exchange_rate(
        self, asset_a: Token | NativeToken, asset_b: Token | NativeToken, amount: int
    ) -> int:
        simulated_pricing_info = self.contract.query(
            {
                "simulation": {
                    "offer_asset": {
                        "info": token_to_asset_info(asset_a),
                        "amount": amount,
                    },
                    "ask_asset_info": {token_to_asset_info(asset_b)},
                }
            }
        )

        return simulated_pricing_info["return_amount"]

    def exchange_rate_asset_a(self, amount: int) -> int:
        return self.__exchange_rate(self.asset_a, self.asset_b)

    def exchange_rate_asset_b(self, amount: int) -> int:
        return self.__exchange_rate(self.asset_b, self.asset_a)

    def asset_a(self) -> str:
        return token_to_addr(self.asset_a)

    def asset_b(self) -> str:
        return token_to_addr(self.asset_b)
