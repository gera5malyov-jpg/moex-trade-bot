import os
from decimal import Decimal

import requests


ACCOUNT_NAME = os.getenv("TINVEST_SANDBOX_ACCOUNT_NAME", "github-moex-trade-bot")
INITIAL_BALANCE_RUB = Decimal("1000000")

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
    if not response.ok:
        raise RuntimeError(
            f"{method} failed with HTTP {response.status_code}: {response.text[:1500]}"
        )
    return response.json()


def money_to_decimal(value):
    if not value:
        return Decimal("0")
    units = Decimal(str(value.get("units", "0")))
    nano = Decimal(str(value.get("nano", 0))) / Decimal("1000000000")
    return units + nano


def get_rub_cash(headers, account_id):
    data = post(
        headers,
        "GetSandboxWithdrawLimits",
        {"accountId": account_id},
    )
    for item in data.get("money") or []:
        if str(item.get("currency", "")).upper() == "RUB":
            return money_to_decimal(item)
    return Decimal("0")


def main():
    token = os.environ["TINVEST_TOKEN"].strip()
    if not token:
        raise RuntimeError("TINVEST_TOKEN is empty")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    accounts_data = post(
        headers,
        "GetSandboxAccounts",
        {"status": "ACCOUNT_STATUS_OPEN"},
    )
    accounts = accounts_data.get("accounts") or []
    matches = [
        account for account in accounts
        if str(account.get("name", "")).strip() == ACCOUNT_NAME
    ]

    created = False
    if matches:
        account = matches[0]
        account_id = str(account.get("id") or account.get("accountId") or "")
        if not account_id:
            raise RuntimeError("Existing sandbox account has no ID")
    else:
        created_data = post(
            headers,
            "OpenSandboxAccount",
            {"name": ACCOUNT_NAME},
        )
        account_id = str(
            created_data.get("accountId")
            or created_data.get("account_id")
            or ""
        )
        if not account_id:
            raise RuntimeError(
                f"OpenSandboxAccount returned no accountId: {created_data}"
            )
        created = True

    if created:
        funding = post(
            headers,
            "SandboxPayIn",
            {
                "accountId": account_id,
                "amount": {
                    "currency": "RUB",
                    "units": str(int(INITIAL_BALANCE_RUB)),
                    "nano": 0,
                },
            },
        )
        balance = money_to_decimal(funding.get("balance"))
        print(f"Created sandbox account: {ACCOUNT_NAME}")
        print(f"Account ID: {account_id}")
        print(f"Funded with {INITIAL_BALANCE_RUB:,.0f} RUB virtual money.")
        print(f"Current sandbox balance: {balance:,.2f} RUB")
    else:
        balance = get_rub_cash(headers, account_id)
        print(f"Sandbox account already exists: {ACCOUNT_NAME}")
        print(f"Account ID: {account_id}")
        print("No additional money was added.")
        print(f"Current available RUB cash: {balance:,.2f} RUB")

    print("You do NOT need to save the account ID in GitHub Secrets.")
    print("Other workflows discover this sandbox account automatically.")


if __name__ == "__main__":
    main()
