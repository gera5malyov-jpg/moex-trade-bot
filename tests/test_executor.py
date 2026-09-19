import unittest
from datetime import datetime, timezone

from tradebot.executor import _validate_new_entry_window


class ExecutorWindowTests(unittest.TestCase):
    def test_allows_main_session_after_opening_buffer(self):
        _validate_new_entry_window(
            datetime(2026, 9, 21, 7, 30, tzinfo=timezone.utc)
        )

    def test_rejects_first_fifteen_minutes(self):
        with self.assertRaises(RuntimeError):
            _validate_new_entry_window(
                datetime(2026, 9, 21, 6, 55, tzinfo=timezone.utc)
            )

    def test_rejects_after_last_entry_cutoff(self):
        with self.assertRaises(RuntimeError):
            _validate_new_entry_window(
                datetime(2026, 9, 21, 14, 45, tzinfo=timezone.utc)
            )

    def test_rejects_weekend_session(self):
        with self.assertRaises(RuntimeError):
            _validate_new_entry_window(
                datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)
            )


if __name__ == "__main__":
    unittest.main()
