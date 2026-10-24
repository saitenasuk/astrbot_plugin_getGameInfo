import tempfile
import unittest
from pathlib import Path

from tests.support import main


class RuntimeStateTests(unittest.TestCase):
    def test_last_check_events_and_counters_survive_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            state = main.RuntimeState()
            state.attach_storage(path)
            state.start_check(trigger="manual", dry_run=True)
            state.finish_check(
                ok=True,
                trigger="manual",
                dry_run=True,
                duration_ms=42,
                games_seen=2,
                new_games=1,
            )
            state.add_event(
                "warning", "push_fail", "群 123 推送失败", group_id="123"
            )
            state.save()

            loaded = main.RuntimeState()
            loaded.attach_storage(path)
            loaded.load()

            self.assertTrue(loaded.last_check["ok"])
            self.assertEqual(loaded.last_check["duration_ms"], 42)
            self.assertEqual(loaded.today_counts()["checks"], 1)
            self.assertEqual(loaded.events()[0]["message"], "群 123 推送失败")
            self.assertGreater(loaded.add_event("info", "check_ok", "续接")["id"], 0)

    def test_corrupt_status_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text("{not-json", encoding="utf-8")
            state = main.RuntimeState()
            state.attach_storage(path)
            state.load()
            self.assertIsNone(state.last_check)
            self.assertEqual(state.events(), [])
