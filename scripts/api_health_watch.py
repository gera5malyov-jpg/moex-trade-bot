#!/usr/bin/env python3
"""T-Invest API health: read-only, email only on an authenticated API failure."""
import json
import os
import smtplib
import time
from email.message import EmailMessage
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


def alert(reason):
    user = os.getenv("MAIL_USER", "").strip()
    password = os.getenv("MAIL_APP_PASSWORD", "")
    if not user or not password:
        print("::error::API failed; email SMTP secrets not available")
        return False
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = "shop@office-mag.com"
    msg["Subject"] = "ВАЖНО !!! ВНИМАНИЕ !! Не работает API: Т-Инвестиции Sandbox"
    msg.set_content("Проверка API торгового бота выявила проблему.\n\nПричина: " +
                    reason + "\nПроверка: " + os.getenv("RUN_URL", "") +
                    "\nЗапрос только для чтения, сделок не совершалось.")
    try:
        with smtplib.SMTP(os.getenv("MAIL_SMTP_HOST") or "smtp.gmail.com", 587, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
        print("Failure-only API alert delivered")
        return True
    except (OSError, smtplib.SMTPException) as err:
        print("::error::Cannot send alert:", type(err).__name__)
        return False


def main():
    status, reason = check()
    print("T-Invest API status:", status, reason)
    if status != "FAIL":
        print("No outage, no email")
        return 0
    return 1 if alert(reason) else 1


if __name__ == "__main__":
    raise SystemExit(main())
