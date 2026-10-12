"""Regression guards for the Sandbox-only trading orchestration.

These checks intentionally use the standard library so that scheduling and
fail-closed command gates cannot be removed unnoticed by future refactors.
They complement, but do not replace, a live broker smoke test.
"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


class SandboxWorkflowSafetyTests(unittest.TestCase):
    def test_primary_scheduler_continues_analysis_after_smoke_failure(self):
        text = workflow("sandbox-daily-controls.yml")
        self.assertIn("id: readiness", text)
        self.assertIn("continue-on-error: true", text)
        self.assertIn("if: steps.readiness.outcome == 'success'", text)
        self.assertIn("SCANNER_ANALYSIS_ONLY_MAX_SIGNALS: \"2\"", text)
        self.assertIn("Surface readiness failure after safety and scan stages", text)
        self.assertIn("group: sandbox-daily-controls", text)

    def test_independent_market_scan_is_analysis_safe(self):
        text = workflow("sandbox-market-scan.yml")
        self.assertIn('cron: "22 7-14 * * 1-5"', text)
        self.assertIn('TRADING_ENABLED: "false"', text)
        self.assertIn('SCANNER_ANALYSIS_ONLY_MAX_SIGNALS: "2"', text)
        self.assertIn("TINVEST_SANDBOX_ACCOUNT_NAME:", text)

    def test_command_backup_serializes_with_primary_scheduler(self):
        text = workflow("process-trade-commands.yml")
        self.assertIn('cron: "35,40 7-14 * * 1-5"', text)
        self.assertIn("group: sandbox-daily-controls", text)
        self.assertIn("TINVEST_SANDBOX_ACCOUNT_NAME:", text)
        self.assertIn("python scripts/process_commands.py", text)
        self.assertIn("python scripts/monitor_lifecycle.py", text)

    def test_manual_scan_routes_allow_analysis_only(self):
        for filename in (
            "sandbox-pulse-runtime.yml",
            "sandbox-trading-runtime.yml",
        ):
            with self.subTest(filename=filename):
                self.assertIn(
                    'SCANNER_ANALYSIS_ONLY_MAX_SIGNALS: "2"',
                    workflow(filename),
                )


if __name__ == "__main__":
    unittest.main()
