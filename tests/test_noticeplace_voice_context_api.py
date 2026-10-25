"""The Secretary reads the NoticePlace context over the authenticated API.

UserIO never opens the store of NoticePlace. The read goes through the
authenticated read API, the audience of the owner stays the same, and a
transport failure stays a clear failure instead of a silent empty list.
"""

from __future__ import annotations

import json
import unittest
import urllib.error
from pathlib import Path

from universal_userio.voice.noticeplace_voice_context import (
    NoticePlaceContextError,
    NoticePlaceVoiceStore,
    asks_recent_repair,
    service_search,
)


class Response:
    def __init__(self, payload: dict, status: int = 200):
        self.payload = json.dumps(payload).encode()
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.payload


class VoiceContextApiTests(unittest.TestCase):
    def _store(self, runner, **kwargs):
        return NoticePlaceVoiceStore(
            base_url="https://notify.example.org", token="read-token",
            project="*", owner_audiences=True, runner=runner, **kwargs)

    def test_the_context_is_read_over_the_authenticated_api(self):
        calls = []

        def runner(request, timeout=None):
            calls.append(request)
            return Response({"schema": "noticeplace.incident.context.v1", "audiences": ["me", "health"],
                             "total_in_scope": 1, "returned": 1, "truncated": False,
                             "incidents": [{"id": "inc_1", "project": "hermes", "state": "open",
                                            "severity": "critical", "title": "Gateway unavailable",
                                            "body": "three checks failed",
                                            "updated_at": 1792960000.0, "resolved_at": None}]})

        store = self._store(runner)
        result = store.list_incidents("gateway")

        self.assertEqual(1, result["returned"])
        self.assertEqual("Gateway unavailable", result["incidents"][0]["title"])
        self.assertEqual(["me", "health"], result["audiences"])
        self.assertEqual(1, len(calls))
        request = calls[0]
        self.assertTrue(request.full_url.startswith("https://notify.example.org/v1/incidents?"))
        self.assertIn("query=gateway", request.full_url)
        self.assertEqual("Bearer read-token", request.get_header("Authorization"))

    def test_the_store_of_noticeplace_is_never_opened(self):
        """The module must not import the NoticePlace storage at all."""
        source = Path(__file__).resolve().parents[1] / "src" / "universal_userio" / "voice" / "noticeplace_voice_context.py"
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("sqlite3", text)
        self.assertNotIn("notification_center", text)
        self.assertNotIn("NOTICEPLACE_DB", text)

    def test_an_unreachable_api_is_a_clear_failure(self):
        def runner(request, timeout=None):
            raise urllib.error.URLError("connection refused")

        store = self._store(runner)
        with self.assertRaises(NoticePlaceContextError):
            store.list_incidents()

    def test_an_http_refusal_is_a_clear_failure(self):
        def runner(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

        store = self._store(runner)
        with self.assertRaises(NoticePlaceContextError):
            store.list_incidents()

    def test_a_missing_configuration_never_silently_returns_nothing(self):
        store = NoticePlaceVoiceStore(base_url="", token="", owner_audiences=True)
        with self.assertRaises(NoticePlaceContextError):
            store.list_incidents()

    def test_one_incident_carries_the_linked_session_only(self):
        def runner(request, timeout=None):
            return Response({"id": "inc_2", "project": "hermes", "state": "open",
                              "severity": "critical", "title": "Gateway unavailable",
                              "body": "three checks failed", "updated_at": 1792960000.0,
                              "resolved_at": None, "has_session": True,
                              "session_id": "01a1653b-d774", "session_harness": "codex"})

        result = self._store(runner).incident("inc_2")
        self.assertTrue(result["has_session"])
        self.assertEqual("01a1653b-d774", result["session_id"])
        self.assertEqual("codex", result["session_harness"])

    def test_the_service_alias_still_expands_to_its_own_terms(self):
        self.assertEqual("autosell", service_search("что с автопродажом"))
        self.assertEqual("", service_search("шлюз не отвечает"))
        self.assertTrue(asks_recent_repair("ты это починил?"))
        self.assertFalse(asks_recent_repair("какие у меня дела"))


if __name__ == "__main__":
    unittest.main()
