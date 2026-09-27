#!/usr/bin/env python3
import json
import os
import re
import smtplib
import ssl
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

MAP_URL = "https://gpnbonus.ru/fuel/refuel-map"
TARGET_STATION = "АЗС №17"
TARGET_ADDRESS = "Санкт-Петербург, Московское шоссе, 46, корпус 3"
TARGET_HINTS = ("московское", "46")
MAIL_TO = os.environ.get("MAIL_TO", "gera5@list.ru")
MAIL_USER = os.environ.get("MAIL_USER", "").strip()
MAIL_APP_PASSWORD = os.environ.get("MAIL_APP_PASSWORD", "").strip()
MAIL_SMTP_HOST = os.environ.get("MAIL_SMTP_HOST", "smtp.gmail.com").strip()
MAIL_SMTP_PORT = int(os.environ.get("MAIL_SMTP_PORT", "465"))

# Approximate location of the target area. It is only used so the official map
# loads the relevant Saint Petersburg cluster; the final station match is done
# strictly by station number/address.
SPB_LAT = 59.81
SPB_LON = 30.35

FUEL_PATTERNS = [
    (r"\bG\s*[-–]?\s*100\b", "G-100"),
    (r"\bG\s*[-–]?\s*95\b", "G-95"),
    (r"\bАИ\s*[-–]?\s*100\b|(?<!\d)100(?!\d)", "100"),
    (r"\bАИ\s*[-–]?\s*98\b|(?<!\d)98(?!\d)", "98"),
    (r"\bАИ\s*[-–]?\s*95\b|(?<!\d)95(?!\d)", "95"),
    (r"\bАИ\s*[-–]?\s*92\b|(?<!\d)92(?!\d)", "92"),
    (r"\bДТ\b|\bДИЗЕЛЬ(?:НОЕ)?\b", "ДТ"),
]


def now_msk() -> datetime:
    return datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Moscow"))


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def station_window(text: str) -> str:
    low = text.lower()
    positions = []
    for marker in ("азс №17", "азс n17", "московское шоссе", "московское 46"):
        p = low.find(marker)
        if p >= 0:
            positions.append(p)
    if not positions:
        return text[-5000:]
    p = min(positions)
    return text[max(0, p - 800): p + 3500]


def extract_available_fuels(text: str) -> list[str]:
    block = station_window(text)
    # Prefer the explicit "В наличии" section from the official station card.
    m = re.search(r"В\s+наличии\s*:\s*(.{0,1400})", block, re.I | re.S)
    source = m.group(1) if m else block

    fuels = []
    for pattern, label in FUEL_PATTERNS:
        if re.search(pattern, source, re.I):
            fuels.append(label)

    # Guard against accidentally treating timestamps/prices as fuel grades.
    # Keep numeric grades only when the surrounding text looks like a fuel list.
    if not m:
        contextual = re.search(
            r"(топлив|в наличии|бензин|дт|g[-\s]?95|аи[-\s]?\d{2,3})",
            source,
            re.I,
        )
        if not contextual:
            fuels = [f for f in fuels if f in ("G-95", "G-100", "ДТ")]

    order = ["G-100", "G-95", "100", "98", "95", "92", "ДТ"]
    return [x for x in order if x in fuels]


def target_visible(text: str) -> bool:
    low = text.lower()
    return (
        ("азс №17" in low or "азс n17" in low)
        and "москов" in low
        and "46" in low
    ) or ("московское шоссе" in low and "46" in low and "азс" in low)


def candidate_from_json(obj) -> str | None:
    try:
        dumped = json.dumps(obj, ensure_ascii=False)
    except Exception:
        return None
    low = dumped.lower()
    if "москов" not in low or "46" not in low:
        return None
    if "азс" not in low and "station" not in low:
        return None
    return dumped


def read_network_candidates(driver) -> list[str]:
    found = []
    try:
        logs = driver.get_log("performance")
    except Exception:
        return found

    for entry in logs:
        try:
            msg = json.loads(entry["message"])["message"]
            if msg.get("method") != "Network.responseReceived":
                continue
            params = msg.get("params", {})
            resp = params.get("response", {})
            mime = (resp.get("mimeType") or "").lower()
            if "json" not in mime and "javascript" not in mime and "text" not in mime:
                continue
            request_id = params.get("requestId")
            if not request_id:
                continue
            body_obj = driver.execute_cdp_cmd(
                "Network.getResponseBody", {"requestId": request_id}
            )
            body = body_obj.get("body", "")
            if not body or len(body) > 5_000_000:
                continue
            low = body.lower()
            if "москов" in low and "46" in low:
                found.append(body)
        except Exception:
            continue
    return found


def click_search_result(driver):
    # Try any visible input; the site has changed its markup before, so avoid
    # relying on one brittle CSS class.
    inputs = driver.find_elements(By.CSS_SELECTOR, "input")
    query = "Московское шоссе 46"
    for el in inputs:
        try:
            if not el.is_displayed() or not el.is_enabled():
                continue
            placeholder = (el.get_attribute("placeholder") or "").lower()
            aria = (el.get_attribute("aria-label") or "").lower()
            typ = (el.get_attribute("type") or "").lower()
            if any(k in placeholder + " " + aria for k in ("поиск", "адрес", "азс")) or typ in ("search", "text", ""):
                el.clear()
                el.send_keys(query)
                el.send_keys(Keys.ENTER)
                time.sleep(4)
                break
        except Exception:
            continue

    # Click the smallest visible element that contains the exact target address.
    candidates = driver.find_elements(
        By.XPATH,
        "//*[contains(translate(normalize-space(.), 'МОСКОВСКОЕ', 'московское'), 'московское') and contains(normalize-space(.), '46')]",
    )
    ranked = []
    for el in candidates:
        try:
            if not el.is_displayed():
                continue
            txt = normalize(el.text)
            if not txt or len(txt) > 260:
                continue
            score = 0
            low = txt.lower()
            if "московское шоссе" in low:
                score += 5
            if "46" in low:
                score += 3
            if "санкт" in low:
                score += 2
            ranked.append((score, len(txt), el))
        except Exception:
            continue
    if ranked:
        ranked.sort(key=lambda x: (-x[0], x[1]))
        try:
            driver.execute_script("arguments[0].click();", ranked[0][2])
            time.sleep(4)
        except Exception:
            pass


def scrape_official_map() -> tuple[list[str], str, str]:
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=430,1200")
    options.add_argument("--lang=ru-RU")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
    )
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    driver = webdriver.Chrome(options=options)
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd(
            "Emulation.setGeolocationOverride",
            {"latitude": SPB_LAT, "longitude": SPB_LON, "accuracy": 100},
        )
        driver.execute_cdp_cmd(
            "Browser.grantPermissions",
            {"origin": "https://gpnbonus.ru", "permissions": ["geolocation"]},
        )

        driver.set_page_load_timeout(60)
        driver.get(MAP_URL)
        time.sleep(8)

        # Dismiss common cookie/consent buttons if present.
        for label in ("Принять", "Согласен", "Понятно", "Хорошо"):
            try:
                elems = driver.find_elements(By.XPATH, f"//*[normalize-space(text())='{label}']")
                for el in elems:
                    if el.is_displayed():
                        driver.execute_script("arguments[0].click();", el)
                        time.sleep(1)
                        raise StopIteration
            except StopIteration:
                break
            except Exception:
                pass

        click_search_result(driver)

        body_text = driver.find_element(By.TAG_NAME, "body").text
        network = read_network_candidates(driver)

        # If station card is already visible, prefer it.
        if target_visible(body_text):
            fuels = extract_available_fuels(body_text)
            if fuels:
                return fuels, normalize(station_window(body_text)), MAP_URL

        # Otherwise inspect relevant JSON/text responses loaded by the official map.
        for raw in network:
            try:
                obj = json.loads(raw)
                candidate = candidate_from_json(obj)
            except Exception:
                candidate = raw if ("москов" in raw.lower() and "46" in raw.lower()) else None
            if not candidate:
                continue
            fuels = extract_available_fuels(candidate)
            if fuels:
                return fuels, normalize(candidate[:4000]), MAP_URL

        # Last DOM attempt: click any visible AZS №17 label and re-read.
        for xpath in (
            "//*[contains(normalize-space(.), 'АЗС №17')]",
            "//*[contains(normalize-space(.), 'АЗС N17')]",
        ):
            try:
                els = driver.find_elements(By.XPATH, xpath)
                els = [e for e in els if e.is_displayed() and len(normalize(e.text)) < 300]
                if els:
                    driver.execute_script("arguments[0].click();", els[0])
                    time.sleep(3)
                    body_text = driver.find_element(By.TAG_NAME, "body").text
                    fuels = extract_available_fuels(body_text)
                    if target_visible(body_text) and fuels:
                        return fuels, normalize(station_window(body_text)), MAP_URL
            except Exception:
                pass

        raise RuntimeError(
            "Официальная карта открылась, но карточка АЗС №17 по адресу "
            "Московское шоссе, 46 к3 не была уверенно распознана."
        )
    finally:
        driver.quit()


def send_email(subject: str, body: str):
    if not MAIL_USER or not MAIL_APP_PASSWORD:
        raise RuntimeError("MAIL_USER/MAIL_APP_PASSWORD are not configured in GitHub Actions secrets.")

    msg = EmailMessage()
    msg["From"] = MAIL_USER
    msg["To"] = MAIL_TO
    msg["Subject"] = subject
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(MAIL_SMTP_HOST, MAIL_SMTP_PORT, context=context, timeout=30) as smtp:
        smtp.login(MAIL_USER, MAIL_APP_PASSWORD)
        smtp.send_message(msg)


def main():
    checked = now_msk()
    stamp = checked.strftime("%d.%m.%Y %H:%M МСК")
    try:
        fuels, raw, source = scrape_official_map()
        fuel_text = ", ".join(fuels)
        subject = f"АЗС №17 — в наличии: {fuel_text}"
        body = (
            f"{TARGET_STATION}\n"
            f"{TARGET_ADDRESS}\n\n"
            f"Проверено: {stamp}\n"
            f"В наличии: {fuel_text}\n"
            f"Источник: официальная онлайн-карта Газпромнефть\n"
            f"{source}\n\n"
            f"Фрагмент карточки/ответа:\n{raw[:1800]}\n"
        )
        print(f"OK {stamp}: {fuel_text}")
    except Exception as exc:
        subject = "АЗС №17 — не удалось получить наличие топлива"
        body = (
            f"{TARGET_STATION}\n"
            f"{TARGET_ADDRESS}\n\n"
            f"Проверено: {stamp}\n"
            f"Ошибка парсера: {type(exc).__name__}: {exc}\n"
            f"Источник: {MAP_URL}\n"
        )
        print(body)

    send_email(subject, body)


if __name__ == "__main__":
    main()
