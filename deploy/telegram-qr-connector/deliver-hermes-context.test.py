import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("deliver-hermes-context.py")
SPEC = importlib.util.spec_from_file_location("deliver_hermes_context", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeDB:
    def __init__(self):
        self.calls = []
        self.closed = False

    def append_delegation_delivery(self, session_id, content, metadata):
        self.calls.append((session_id, content, metadata))

    def close(self):
        self.closed = True


class DeliverHermesContextTest(unittest.TestCase):
    def test_records_before_sending(self):
        db = FakeDB()
        commands = []
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.txt"
            report.write_text("Автор: Дмитрий\nВарианты: 1, 2, 3\n", encoding="utf-8")
            MODULE.record_and_send(
                session_id="session-1",
                event_id="event-1",
                report_path=report,
                target="telegram",
                db_factory=lambda: db,
                run=lambda command, check: commands.append((command, check)),
            )

        self.assertTrue(db.closed)
        self.assertEqual(db.calls[0][0:2], ("session-1", "Автор: Дмитрий\nВарианты: 1, 2, 3"))
        self.assertEqual(db.calls[0][2]["delegation_id"], "event-1")
        self.assertIn("send", commands[0][0])
        self.assertEqual(commands[0][0][-3:], ["--file", str(report), "--quiet"])
        self.assertTrue(commands[0][1])


if __name__ == "__main__":
    unittest.main()
