from __future__ import annotations

import email
import imaplib
import json
import smtplib
import ssl
from email.header import decode_header
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Iterable


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            out.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out)


def _extract_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if ctype == "text/plain" and "attachment" not in disp.lower():
                payload = part.get_payload(decode=True)
                if payload is not None:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload is not None:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return ""


def extract_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found in message")

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i+1]
                json.loads(candidate)
                return candidate

    raise ValueError("Unbalanced JSON object")


def send_signal_email(
    *,
    smtp_host: str,
    user: str,
    app_password: str,
    recipient: str,
    signal_id: str,
    json_body: str,
) -> None:
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = recipient
    msg["Subject"] = f"[TRADE-SIGNAL] {signal_id}"
    msg.set_content(
        "Автоматический торговый сигнал. Для ответа сохраните signal_id и auth_token.\n\n"
        + json_body
    )

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, 465, context=context) as smtp:
        smtp.login(user, app_password)
        smtp.send_message(msg)


def iter_unseen_command_messages(
    *,
    imap_host: str,
    user: str,
    app_password: str,
    allowed_from: str,
) -> Iterable[tuple[bytes, str, str]]:
    client = imaplib.IMAP4_SSL(imap_host, 993)
    client.login(user, app_password)
    client.select("INBOX")

    status, data = client.search(None, '(UNSEEN SUBJECT "[TRADE-CMD]")')
    if status != "OK":
        client.logout()
        raise RuntimeError("IMAP search failed")

    try:
        for msg_id in data[0].split():
            status, fetched = client.fetch(msg_id, "(BODY.PEEK[])")
            if status != "OK" or not fetched:
                continue

            raw = fetched[0][1]
            msg = email.message_from_bytes(raw)
            sender = parseaddr(msg.get("From", ""))[1].lower()
            if sender != allowed_from.lower():
                continue

            subject = _decode_header(msg.get("Subject"))
            body = _extract_text(msg)
            yield msg_id, subject, extract_json_object(body)
    finally:
        client.logout()


def mark_seen(
    *,
    imap_host: str,
    user: str,
    app_password: str,
    message_id: bytes,
) -> None:
    client = imaplib.IMAP4_SSL(imap_host, 993)
    client.login(user, app_password)
    client.select("INBOX")
    try:
        client.store(message_id, "+FLAGS", "\\Seen")
    finally:
        client.logout()
