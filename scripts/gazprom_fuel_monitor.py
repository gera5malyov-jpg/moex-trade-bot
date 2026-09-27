#!/usr/bin/env python3
import json
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://gpnbonus.ru"
MAP_URL = f"{BASE_URL}/fuel/refuel-map"
LIST_URL = f"{BASE_URL}/api/stations/list"

TARGET_NUMBER = "17"
TARGET_ADDRESS_WORDS = ("москов", "46")
TARGET_LABEL = "АЗС №17"
TARGET_ADDRESS = "Санкт-Петербург, Московское шоссе, 46, корпус 3"

MAIL_TO = os.environ.get("MAIL_TO", "gera5@list.ru").strip()
MAIL_USER = os.environ.get("MAIL_USER", "").strip()
MAIL_APP_PASSWORD = os.environ.get("MAIL_APP_PASSWORD", "").strip()
MAIL_SMTP_HOST = os.environ.get("MAIL_SMTP_HOST", "smtp.gmail.com").strip()
MAIL_SMTP_PORT = int(os.environ.get("MAIL_SMTP_PORT", "465"))

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36 FuelMonitor/1.0"
)

LIST_PAYLOAD = {
    "open": False,
    "wash": False,
    "AZSShopTypeID": False,
    "services": {
        "car": {},
        "payment": {},
        "person": {},
        "station": {},
    },
}


def now_msk() -> datetime:
    return datetime.now(ZoneInfo("Europe/Moscow"))


def base_headers() -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": BASE_URL,
        "Referer": MAP_URL,
        "User-Agent": USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
    }


def station_number(station: dict) -> str:
    for key in ("PNPONumber", "pnpoNumber", "number", "stationNumber"):
        value = station.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def station_text(station: dict) -> str:
    fields = (
        station.get("name"),
        station.get("address"),
        station.get("city"),
        station.get("title"),
    )
    return " ".join(str(v or "") for v in fields).lower()


def station_id(station: dict) -> str:
    for key in ("GPNAZSID", "id", "stationId"):
        value = station.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def choose_target(stations: list[dict]) -> dict:
    exact = []
    address_matches = []

    for station in stations:
        if not isinstance(station, dict):
            continue
        text = station_text(station)
        address_ok = all(word in text for word in TARGET_ADDRESS_WORDS)
        number_ok = station_number(station) == TARGET_NUMBER

        if number_ok and address_ok:
            exact.append(station)
        elif address_ok:
            address_matches.append(station)

    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise RuntimeError(
            f"Найдено несколько АЗС №{TARGET_NUMBER} с адресом Московское шоссе 46; "
            "отказываюсь выбирать наугад."
        )
    if len(address_matches) == 1:
        # Address is sufficiently specific; keep this fallback in case the public
        # list stops exposing PNPONumber but continues exposing the address.
        return address_matches[0]

    raise RuntimeError(
        "Целевая АЗС №17 (Санкт-Петербург, Московское шоссе, 46 к3) "
        "не найдена однозначно в списке Газпромнефть."
    )


def bootstrap_session(session: requests.Session) -> None:
    # A normal same-origin page load may provide session cookies. Failure here is
    # not fatal: the API sometimes works without it.
    try:
        session.get(
            MAP_URL,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "ru-RU,ru;q=0.9",
            },
            timeout=(10, 20),
        )
    except requests.RequestException:
        pass


def fetch_station_detail() -> tuple[dict, dict]:
    session = requests.Session()
    session.headers.update(base_headers())
    bootstrap_session(session)

    response = session.post(
        LIST_URL,
        json=LIST_PAYLOAD,
        timeout=(15, 45),
    )
    response.raise_for_status()
    payload = response.json()

    stations = payload.get("stations", [])
    if not isinstance(stations, list):
        raise RuntimeError("Ответ /api/stations/list не содержит массива stations.")

    target = choose_target(stations)
    sid = station_id(target)
    if not sid:
        raise RuntimeError("У целевой АЗС отсутствует GPNAZSID/id.")

    detail_response = session.post(
        f"{BASE_URL}/api/stations/{sid}",
        data=b"",
        headers={"Content-Type": "application/json"},
        timeout=(15, 45),
    )
    detail_response.raise_for_status()
    detail = detail_response.json()
    return target, detail


def normalize_fuel_name(product: dict) -> str:
    raw = str(
        product.get("shortTitle")
        or product.get("title")
        or product.get("name")
        or ""
    ).strip()
    upper = raw.upper().replace(" ", "")

    aliases = {
        "АИ92": "92",
        "АИ-92": "92",
        "92": "92",
        "АИ95": "95",
        "АИ-95": "95",
        "95": "95",
        "АИ98": "98",
        "АИ-98": "98",
        "98": "98",
        "АИ100": "100",
        "АИ-100": "100",
        "100": "100",
        "G95": "G-95",
        "G-95": "G-95",
        "G100": "G-100",
        "G-100": "G-100",
        "ДТ": "ДТ",
        "ДТЛ": "ДТ",
        "ДИЗЕЛЬ": "ДТ",
        "ДИЗЕЛЬНОЕТОПЛИВО": "ДТ",
    }
    return aliases.get(upper, raw or "Неизвестное топливо")


def parse_fuels(detail: dict) -> list[dict]:
    items = detail.get("data", [])
    if not isinstance(items, list):
        raise RuntimeError("Карточка АЗС не содержит массива data.")

    result = []
    for item in items:
        if not isinstance(item, dict):
            continue

        product = item.get("product") or {}
        if isinstance(product, list):
            product = product[0] if product else {}
        if not isinstance(product, dict):
            product = {}

        rest = item.get("rest") or {}
        if isinstance(rest, list):
            rest = rest[0] if rest else {}
        if not isinstance(rest, dict):
            rest = {}

        price = item.get("price") or {}
        if isinstance(price, list):
            price = price[0] if price else {}
        if not isinstance(price, dict):
            price = {}

        name = normalize_fuel_name(product)
        available = bool(rest.get("avail"))
        delivery = rest.get("delivery")
        expected = delivery not in (None, False, "", "no", "NO", 0, "0")
        price_value = price.get("price")
        price_since = price.get("since")

        result.append(
            {
                "name": name,
                "available": available,
                "expected": expected,
                "price": price_value,
                "price_since": price_since,
            }
        )

    if not result:
        raise RuntimeError("Карточка АЗС получена, но список видов топлива пуст.")
    return result


def format_report(target: dict, fuels: list[dict]) -> tuple[str, str]:
    checked = now_msk().strftime("%d.%m.%Y %H:%M:%S МСК")
    available = [x["name"] for x in fuels if x["available"]]
    expected = [x["name"] for x in fuels if (not x["available"] and x["expected"])]
    absent = [x["name"] for x in fuels if (not x["available"] and not x["expected"])]

    subject_fuels = ", ".join(available) if available else "топлива нет"
    subject = f"АЗС №17 — в наличии: {subject_fuels}"

    lines = [
        TARGET_LABEL,
        TARGET_ADDRESS,
        "",
        f"Проверено: {checked}",
        f"В наличии: {', '.join(available) if available else 'нет'}",
    ]
    if expected:
        lines.append(f"Ожидается поставка: {', '.join(expected)}")
    if absent:
        lines.append(f"Нет в наличии: {', '.join(absent)}")

    priced = []
    for fuel in fuels:
        if fuel["price"] not in (None, ""):
            priced.append(f'{fuel["name"]}: {fuel["price"]} ₽')
    if priced:
        lines.extend(["", "Цены по источнику:", *priced])

    sid = station_id(target)
    if sid:
        lines.extend(["", f"ID Газпромнефть: {sid}"])

    lines.extend(["Источник: официальные данные карты АЗС Газпромнефть", MAP_URL])
    return subject, "\n".join(lines)


def send_email(subject: str, body: str) -> None:
    if not MAIL_USER or not MAIL_APP_PASSWORD:
        raise RuntimeError(
            "MAIL_USER/MAIL_APP_PASSWORD не настроены в окружении."
        )

    msg = EmailMessage()
    msg["From"] = MAIL_USER
    msg["To"] = MAIL_TO
    msg["Subject"] = subject
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(
        MAIL_SMTP_HOST,
        MAIL_SMTP_PORT,
        context=context,
        timeout=30,
    ) as smtp:
        smtp.login(MAIL_USER, MAIL_APP_PASSWORD)
        smtp.send_message(msg)


def main() -> None:
    try:
        target, detail = fetch_station_detail()
        fuels = parse_fuels(detail)
        subject, body = format_report(target, fuels)
        print(body)
        send_email(subject, body)
    except Exception as exc:
        checked = now_msk().strftime("%d.%m.%Y %H:%M:%S МСК")
        subject = "АЗС №17 — ошибка проверки топлива"
        body = (
            f"{TARGET_LABEL}\n{TARGET_ADDRESS}\n\n"
            f"Проверено: {checked}\n"
            f"Ошибка: {type(exc).__name__}: {exc}\n"
            "Данные о наличии топлива не подменялись и не угадывались."
        )
        print(body)
        send_email(subject, body)
        raise


if __name__ == "__main__":
    main()
