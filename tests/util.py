import json


def deployments() -> dict[str, any]:
    with open("contracts/deployments.json") as f:
        return json.load(f)
