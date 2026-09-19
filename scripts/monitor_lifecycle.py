import json

from tradebot.config import Config
from tradebot.lifecycle import FINAL_STATUSES, monitor_lifecycle_state
from tradebot.mailbox import (
    load_latest_lifecycle_states,
    send_lifecycle_state_email,
)
from tradebot.tinvest import TInvestSandboxClient


def main():
    cfg = Config.from_env()
    states = load_latest_lifecycle_states(
        imap_host=cfg.imap_host,
        user=cfg.mail_user,
        app_password=cfg.mail_app_password,
    )

    active = [
        state
        for state in states
        if str(state.get("status") or "") not in FINAL_STATUSES
        and state.get("environment") == "TINVEST_SANDBOX"
    ]
    if not active:
        print("Active lifecycle states: 0")
        return

    client = TInvestSandboxClient(
        token=cfg.tinvest_token,
        account_name=cfg.sandbox_account_name,
    )

    transitions = 0
    errors = 0
    for state in active:
        signal_id = str(state.get("signal_id") or "")
        try:
            updated = monitor_lifecycle_state(
                client=client,
                state=state,
            )
            if updated != state:
                send_lifecycle_state_email(
                    smtp_host=cfg.smtp_host,
                    user=cfg.mail_user,
                    app_password=cfg.mail_app_password,
                    recipient=cfg.mail_user,
                    signal_id=signal_id,
                    payload=updated,
                )
                print(json.dumps({
                    "signal_id": signal_id,
                    "from": state.get("status"),
                    "to": updated.get("status"),
                }, ensure_ascii=False))
                transitions += 1
        except Exception as exc:
            # Broker-side STOP/TAKE orders remain active even if this monitor
            # run fails. The next scheduled run retries state reconciliation.
            print(
                f"Lifecycle monitor failed for {signal_id}: "
                f"{type(exc).__name__}: {exc}"
            )
            errors += 1

    print(
        f"Active lifecycle states={len(active)}; "
        f"transitions={transitions}; errors={errors}"
    )


if __name__ == "__main__":
    main()
