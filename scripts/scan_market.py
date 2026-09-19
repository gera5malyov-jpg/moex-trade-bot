import json
import os

from tradebot.config import Config
from tradebot.mailbox import send_signal_email
from tradebot.protocol import Signal
from tradebot.scanner import enrich_candidate, scan_candidates
from tradebot.tinvest import TInvestSandboxClient


def main():
    cfg = Config.from_env()
    client = TInvestSandboxClient(
        token=cfg.tinvest_token,
        account_name=cfg.sandbox_account_name,
    )

    max_per_type = int(os.getenv("SCANNER_MAX_PER_TYPE", "1"))
    max_signals = int(os.getenv("SCANNER_MAX_SIGNALS", "6"))

    candidates = scan_candidates(
        client,
        max_per_type=max_per_type,
    )

    sent = 0
    for candidate in candidates[:max_signals]:
        try:
            context = enrich_candidate(client, candidate)
            signal = Signal.create(
                secret=cfg.hmac_secret,
                ticker=candidate["ticker"],
                class_code=candidate["class_code"],
                instrument_uid=candidate["instrument_uid"],
                instrument_type=candidate["instrument_type"],
                execution_capability=False,
                observed_price=candidate["last_price"],
                reason=(
                    "SCANNER CANDIDATE ONLY — "
                    f"{candidate['instrument_type']} "
                    f"{candidate['ticker']} moved "
                    f"{candidate['move_percent']:.4f}% from previous close. "
                    "Requires independent ChatGPT Work review; "
                    "automatic execution disabled."
                ),
                context=context,
            )
            send_signal_email(
                smtp_host=cfg.smtp_host,
                user=cfg.mail_user,
                app_password=cfg.mail_app_password,
                recipient=cfg.mail_to,
                signal_id=signal.signal_id,
                json_body=signal.to_json(),
            )
            print(
                json.dumps(
                    {
                        "signal_id": signal.signal_id,
                        "ticker": signal.ticker,
                        "instrument_type": signal.instrument_type,
                        "execution_capability": signal.execution_capability,
                    },
                    ensure_ascii=False,
                )
            )
            sent += 1
        except Exception as exc:
            print(
                f"Candidate enrichment/send failed for "
                f"{candidate.get('ticker')}: "
                f"{type(exc).__name__}: {exc}"
            )

    print(
        f"Scanner candidates={len(candidates)}; "
        f"analysis-only signals sent={sent}"
    )


if __name__ == "__main__":
    main()
