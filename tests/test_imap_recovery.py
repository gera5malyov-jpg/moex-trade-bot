"""Tests for fail-closed reconnect of the Gmail IMAP transport."""

import imaplib
import unittest
from unittest.mock import patch

from tradebot.mailbox import _authenticated_imap


class _FakeMailbox:
    def __init__(self, *, fail_select=False):
        self.fail_select = fail_select
        self.logged_in = False
        self.selected = None

    def login(self, user, password):
        self.logged_in = True
        return ("OK", [])

    def select(self, mailbox, readonly=False):
        self.selected = (mailbox, readonly)
        if self.fail_select:
            raise imaplib.IMAP4.abort("socket error: EOF")
        return ("OK", [b"0"])


class ImapRecoveryTests(unittest.TestCase):
    @patch("tradebot.mailbox.time.sleep")
    @patch("tradebot.mailbox.imaplib.IMAP4_SSL")
    def test_retries_transient_select_abort(self, connect, sleep):
        first = _FakeMailbox(fail_select=True)
        second = _FakeMailbox()
        connect.side_effect = [first, second]
        result = _authenticated_imap(
            imap_host="imap.example.org", user="user@example.org",
            app_password="not-a-real-secret",
        )
        self.assertIs(result, second)
        self.assertEqual(second.selected, ("INBOX", True))
        self.assertEqual(connect.call_count, 2)
        sleep.assert_called_once_with(1)

    @patch("tradebot.mailbox.time.sleep")
    @patch("tradebot.mailbox.imaplib.IMAP4_SSL")
    def test_auth_failure_does_not_retry(self, connect, sleep):
        connection = _FakeMailbox()
        def reject_login(*args):
            raise imaplib.IMAP4.error("authentication failed")
        connection.login = reject_login
        connect.return_value = connection
        with self.assertRaises(imaplib.IMAP4.error):
            _authenticated_imap(
                imap_host="imap.example.org", user="user@example.org",
                app_password="invalid",
            )
        self.assertEqual(connect.call_count, 1)
        sleep.assert_not_called()

    @patch("tradebot.mailbox.time.sleep")
    @patch("tradebot.mailbox.imaplib.IMAP4_SSL")
    def test_permanent_eof_raises(self, connect, sleep):
        connect.side_effect = imaplib.IMAP4.abort("socket error: EOF")
        with self.assertRaises(imaplib.IMAP4.abort):
            _authenticated_imap(
                imap_host="imap.example.org", user="user@example.org",
                app_password="not-a-real-secret",
            )
        self.assertEqual(connect.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("tradebot.mailbox.imaplib.IMAP4_SSL")
    def test_mark_seen_requires_write_select(self, connect):
        client = _FakeMailbox()
        connect.return_value = client
        result = _authenticated_imap(
            imap_host="imap.example.org", user="user@example.org",
            app_password="not-a-real-secret", readonly=False,
        )
        self.assertIs(result, client)
        self.assertEqual(client.selected, ("INBOX", False))


if __name__ == "__main__":
    unittest.main()
