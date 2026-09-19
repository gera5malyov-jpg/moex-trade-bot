import os
import requests


BASE = (
    "https://sandbox-invest-public-api.tbank.ru/rest/"
    "tinkoff.public.invest.api.contract.v1.SandboxService"
)


def main():
    token = os.environ["TINVEST_TOKEN"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    response = requests.post(
        BASE + "/OpenSandboxAccount",
        headers=headers,
        json={"name": "github-moex-trade-bot"},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    print("Sandbox account created.")
    print("Save accountId as GitHub secret TINVEST_SANDBOX_ACCOUNT_ID:")
    print(data.get("accountId") or data)


if __name__ == "__main__":
    main()
