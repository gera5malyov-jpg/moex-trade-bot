#!/usr/bin/env python3
"""T-Invest API health: read-only, artifact for central notifier."""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

URL = ("https://sandbox-invest-public-api.tbank.ru/rest/"
       "tinkoff.public.invest.api.contract.v1.SandboxService/GetSandboxAccounts")
CERT = Path(__file__).resolve().parents[1] / "certs" / "RussianTrustedRootCA.pem"


def check():
    token = os.getenv("TINVEST_TOKEN", "").strip()
    if not token:
        print("SKIP: no TINVEST_TOKEN; cannot test API")
        return "SKIP", "Ключ для проверки не настроен"
    if not CERT.is_file():
        print("SKIP: RussianTrustedRootCA.pem not found")
        return "SKIP", "Отсутствует сертификат проверки TLS"

    for attempt in range(3):
        try:
            r = requests.post(
                URL,
                json={"status": "ACCOUNT_STATUS_OPEN"},
                headers={"Authorization": "Bearer " + token,
                         "Content-Type": "application/json",
                         "Accept": "application/json"},
                verify=str(CERT), timeout=25)
        except requests.RequestException:
            if attempt < 2:
                time.sleep(6)
                continue
            return "FAIL", "Сервис не отвечает после повторных попыток"
        if r.status_code == 429:
            if attempt < 2:
                time.sleep(15)
                continue
            return "SKIP", "API временно ограничил запросы (429)"
        if r.status_code in (500, 502, 503, 504) and attempt < 2:
            time.sleep(7)
            continue
        if r.status_code in (401, 403):
            return "FAIL", "Авторизация отклонена: HTTP " + str(r.status_code)
        if r.status_code != 200:
            return "FAIL", "HTTP " + str(r.status_code)
        try:
            payload = r.json()
        except ValueError:
            return "FAIL", "Получен некорректный ответ"
        if isinstance(payload, dict) and isinstance(payload.get("accounts"), list):
            return "OK", ""
        return "FAIL", "Неожиданная структура ответа API"
    return "SKIP", "Не удалось подтвердить статус"


def main():
    status, reason = check()
    print("T-Invest API status:", status, reason)
    status = "UNKNOWN" if status == "SKIP" else status
    path = Path("reports") / "tinvest-api-health.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "services": [{"service": "Т-Инвестиции Sandbox", "status": status,
                      "reason": reason}],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("No email from trading repository; central notifier handles alerts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
