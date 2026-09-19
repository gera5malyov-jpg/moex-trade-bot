from __future__ import annotations

import email
import imaplib
import json
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
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
                    return payload.decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace",
                    )
    else:
        payload = msg.get_payload(decode=True)
        if payload is not None:
            return payload.decode(
                msg.get_content_charset() or "utf-8",
                errors="replace",
            )
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
                candidate = text[start : i + 1]
                json.loads(candidate)
                return candidate

    raise ValueError("Unbalanced JSON object")


def _send_text_email(
    *,
    smtp_host: str,
    user: str,
    app_password: str,
    recipient: str,
    subject: str,
    body: str,
    extra_headers: dict[str, str] | None = None,
) -> None:
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = recipient
    msg["Subject"] = subject
    for key, value in (extra_headers or {}).items():
        msg[key] = value
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, 465, context=context) as smtp:
        smtp.login(user, app_password)
        smtp.send_message(msg)


def send_signal_email(
    *,
    smtp_host: str,
    user: str,
    app_password: str,
    recipient: str,
    signal_id: str,
    json_body: str,
    instrument_uid: str | None = None,
) -> None:
    _send_text_email(
        smtp_host=smtp_host,
        user=user,
        app_password=app_password,
        recipient=recipient,
        subject=f"[TRADE-SIGNAL] {signal_id}",
        body=(
            "Автоматический торговый сигнал. "
            "Для ответа сохраните неизменяемые поля протокола.\n\n"
            + json_body
        ),
        extra_headers={
            "X-Trade-Sent-At": datetime.now(timezone.utc).isoformat(),
            **(
                {"X-Trade-Instrument-Uid": instrument_uid}
                if instrument_uid
                else {}
            ),
        },
    )


def send_execution_receipt(
    *,
    smtp_host: str,
    user: str,
    app_password: str,
    recipient: str,
    signal_id: str,
    payload: dict,
) -> None:
    # Never include auth_token or broker access tokens in the journal.
    safe = dict(payload)
    safe.pop("auth_token", None)
    _send_text_email(
        smtp_host=smtp_host,
        user=user,
        app_password=app_password,
        recipient=recipient,
        subject=f"[TRADE-EXEC] {signal_id}",
        body=json.dumps(safe, ensure_ascii=False, indent=2, default=str),
    )



def send_lifecycle_state_email(
    *,
    smtp_host: str,
    user: str,
    app_password: str,
    recipient: str,
    signal_id: str,
    payload: dict,
) -> None:
    _send_text_email(
        smtp_host=smtp_host,
        user=user,
        app_password=app_password,
        recipient=recipient,
        subject=f"[TRADE-LIFECYCLE] {signal_id}",
        body=json.dumps(payload, ensure_ascii=False, indent=2, default=str),
    )


def send_risk_baseline_email(
    *,
    smtp_host: str,
    user: str,
    app_password: str,
    recipient: str,
    trading_date: str,
    payload: dict,
) -> None:
    _send_text_email(
        smtp_host=smtp_host,
        user=user,
        app_password=app_password,
        recipient=recipient,
        subject=f"[TRADE-RISK-BASELINE] {trading_date}",
        body=json.dumps(payload, ensure_ascii=False, indent=2, default=str),
    )


def send_daily_data_email(
    *,
    smtp_host: str,
    user: str,
    app_password: str,
    recipient: str,
    trading_date: str,
    payload: dict,
) -> None:
    _send_text_email(
        smtp_host=smtp_host,
        user=user,
        app_password=app_password,
        recipient=recipient,
        subject=f"[TRADE-DAILY-DATA] {trading_date}",
        body=json.dumps(payload, ensure_ascii=False, indent=2, default=str),
    )



def _select_special_mailbox(
    client: imaplib.IMAP4_SSL,
    special_flag: str,
) -> None:
    status, boxes = client.list()
    if status != "OK":
        raise RuntimeError("IMAP mailbox listing failed")
    wanted = special_flag.lower()
    for raw in boxes or []:
        text = raw.decode("utf-8", errors="replace")
        if wanted not in text.lower():
            continue
        # Keep the mailbox token exactly as returned by IMAP. It is usually
        # quoted (for example "[Gmail]/Sent Mail") and may contain spaces.
        marker = ") "
        pos = text.find(marker)
        if pos < 0:
            continue
        rest = text[pos + len(marker):].strip()
        # Drop the hierarchy delimiter (usually "/") and retain the name.
        if rest.startswith('"'):
            end = rest.find('"', 1)
            if end >= 0:
                rest = rest[end + 1 :].strip()
        mailbox = rest
        if not mailbox:
            continue
        selected, _ = client.select(mailbox, readonly=True)
        if selected == "OK":
            return
    raise RuntimeError(f"IMAP special mailbox not found: {special_flag}")


def has_recent_signal_for_instrument(
    *,
    imap_host: str,
    user: str,
    app_password: str,
    instrument_uid: str,
    within_minutes: int,
) -> bool:
    if within_minutes <= 0:
        return False

    client = imaplib.IMAP4_SSL(imap_host, 993)
    client.login(user, app_password)
    try:
        _select_special_mailbox(client, "\\Sent")
        status, data = client.search(
            None,
            f'(HEADER X-Trade-Instrument-Uid "{instrument_uid}")',
        )
        if status != "OK":
            raise RuntimeError("IMAP recent signal search failed")

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
        for msg_id in reversed(data[0].split()):
            status, fetched = client.fetch(
                msg_id,
                "(BODY.PEEK[HEADER.FIELDS (X-Trade-Sent-At)])",
            )
            if status != "OK" or not fetched:
                continue
            raw = fetched[0][1]
            msg = email.message_from_bytes(raw)
            sent_at_raw = str(msg.get("X-Trade-Sent-At") or "").strip()
            if not sent_at_raw:
                continue
            try:
                sent_at = datetime.fromisoformat(
                    sent_at_raw.replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if sent_at.tzinfo is None:
                continue
            if sent_at.astimezone(timezone.utc) >= cutoff:
                return True
        return False
    finally:
        client.logout()

def _imap_has_exact_subject_from(
    *,
    imap_host: str,
    user: str,
    app_password: str,
    subject: str,
    allowed_from: str,
) -> bool:
    client = imaplib.IMAP4_SSL(imap_host, 993)
    client.login(user, app_password)
    client.select("INBOX")
    try:
        status, data = client.search(None, "SUBJECT", f'"{subject}"')
        if status != "OK":
            raise RuntimeError("IMAP search failed")

        for msg_id in data[0].split():
            status, fetched = client.fetch(
                msg_id,
                "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])",
            )
            if status != "OK" or not fetched:
                continue
            raw = fetched[0][1]
            msg = email.message_from_bytes(raw)
            sender = parseaddr(msg.get("From", ""))[1].lower()
            actual_subject = _decode_header(msg.get("Subject")).strip()
            if (
                sender == allowed_from.lower()
                and actual_subject == subject
            ):
                return True
        return False
    finally:
        client.logout()



def load_latest_lifecycle_states(
    *,
    imap_host: str,
    user: str,
    app_password: str,
) -> list[dict]:
    client = imaplib.IMAP4_SSL(imap_host, 993)
    client.login(user, app_password)
    client.select("INBOX", readonly=True)
    try:
        status, data = client.search(None, "SUBJECT", '"[TRADE-LIFECYCLE]"')
        if status != "OK":
            raise RuntimeError("IMAP lifecycle search failed")

        latest: dict[str, tuple[int, dict]] = {}
        for msg_id in data[0].split():
            status, fetched = client.fetch(msg_id, "(BODY.PEEK[])")
            if status != "OK" or not fetched:
                continue
            raw = fetched[0][1]
            msg = email.message_from_bytes(raw)
            sender = parseaddr(msg.get("From", ""))[1].lower()
            subject = _decode_header(msg.get("Subject")).strip()
            if sender != user.lower() or not subject.startswith("[TRADE-LIFECYCLE] "):
                continue
            try:
                payload = json.loads(extract_json_object(_extract_text(msg)))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            signal_id = str(payload.get("signal_id") or "").strip()
            if not signal_id:
                continue
            numeric_id = int(msg_id)
            previous = latest.get(signal_id)
            if previous is None or numeric_id > previous[0]:
                latest[signal_id] = (numeric_id, payload)

        return [item[1] for item in latest.values()]
    finally:
        client.logout()


def has_risk_baseline(
    *,
    imap_host: str,
    user: str,
    app_password: str,
    trading_date: str,
) -> bool:
    return _imap_has_exact_subject_from(
        imap_host=imap_host,
        user=user,
        app_password=app_password,
        subject=f"[TRADE-RISK-BASELINE] {trading_date}",
        allowed_from=user,
    )


def load_risk_baseline(
    *,
    imap_host: str,
    user: str,
    app_password: str,
    trading_date: str,
) -> dict:
    subject = f"[TRADE-RISK-BASELINE] {trading_date}"
    client = imaplib.IMAP4_SSL(imap_host, 993)
    client.login(user, app_password)
    client.select("INBOX", readonly=True)
    try:
        status, data = client.search(None, "SUBJECT", f'"{subject}"')
        if status != "OK":
            raise RuntimeError("IMAP risk baseline search failed")

        matches: list[bytes] = []
        for msg_id in data[0].split():
            status, fetched = client.fetch(msg_id, "(BODY.PEEK[])")
            if status != "OK" or not fetched:
                continue
            raw = fetched[0][1]
            msg = email.message_from_bytes(raw)
            sender = parseaddr(msg.get("From", ""))[1].lower()
            actual_subject = _decode_header(msg.get("Subject")).strip()
            if sender == user.lower() and actual_subject == subject:
                matches.append(raw)

        if len(matches) != 1:
            raise RuntimeError(
                f"Expected exactly one risk baseline for {trading_date}, "
                f"found {len(matches)}"
            )
        payload = json.loads(extract_json_object(_extract_text(email.message_from_bytes(matches[0]))))
        if not isinstance(payload, dict):
            raise RuntimeError("Risk baseline payload must be an object")
        return payload
    finally:
        client.logout()


def has_execution_receipt(
    *,
    imap_host: str,
    user: str,
    app_password: str,
    allowed_from: str,
    signal_id: str,
) -> bool:
    return _imap_has_exact_subject_from(
        imap_host=imap_host,
        user=user,
        app_password=app_password,
        subject=f"[TRADE-EXEC] {signal_id}",
        allowed_from=allowed_from,
    )


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

    status, data = client.search(
        None,
        '(UNSEEN SUBJECT "[TRADE-CMD]")',
    )
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
