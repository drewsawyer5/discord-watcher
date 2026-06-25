import unittest
from unittest.mock import patch

import nuc_watchdog as nw


class SvcActiveRetryTests(unittest.TestCase):
    def test_returns_active_on_first_try(self):
        with patch.object(nw, "ssh", return_value=(0, "active\n", "")) as mock_ssh:
            self.assertEqual(nw.svc_active("pa-bot"), "active")
        self.assertEqual(mock_ssh.call_count, 1)

    def test_retries_transient_blip_then_reports_active(self):
        # First check returns empty (SSH blip), second returns active.
        seq = [(255, "", "ssh timeout"), (0, "active\n", "")]
        with patch.object(nw, "ssh", side_effect=seq) as mock_ssh, patch.object(nw.time, "sleep"):
            self.assertEqual(nw.svc_active("discord-watcher"), "active")
        self.assertEqual(mock_ssh.call_count, 2)

    def test_persistent_inactive_reported_after_retries(self):
        with patch.object(nw, "ssh", return_value=(0, "inactive\n", "")) as mock_ssh, patch.object(nw.time, "sleep"):
            self.assertEqual(nw.svc_active("pa-bot"), "inactive")
        self.assertEqual(mock_ssh.call_count, nw.SVC_CHECK_RETRIES)


if __name__ == "__main__":
    unittest.main()
