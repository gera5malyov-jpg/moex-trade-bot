import os
import requests


BASE = (
    "https://sandbox-invest-public-api.tbank.ru/rest/"
    "tinkoff.public.invest.api.contract.v1.SandboxService"
)


def post(headers, method, payload):
    response = requests.post(
        BASE + "/" + method,
        headers=headers,
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def main():
    token = os.environ["TINVEST_TOKEN"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    data = post(
        headers,
        "OpenSandboxAccount",
        {"name": "github-moex-trade-bot"},
    )
    account_id = data.get("accountId") or data.get("account_id")
    if not account_id:
        raise RuntimeError(f"OpenSandboxAccount returned no accountId: {data}")

    funding = post(
        headers,
        "SandboxPayIn",
        {
            "accountId": account_id,
            "amount": {
                "currency": "rub",
                "units": "1000000",
                "nano": 0,
            },
        },
    )

    print("Sandbox account created and funded with 1,000,000 RUB test money.")
    print("Save this value as GitHub secret TINVEST_SANDBOX_ACCOUNT_ID:")
    print(account_id)
    print("SandboxPayIn completed:", funding)


if __name__ == "__main__":
    main()
