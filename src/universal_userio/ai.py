"""AI adapter boundary. It receives business conversation context, never provider sessions."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any

from .contracts import InboxMessage


class OpenAICompatibleDraftGenerator:
    def __init__(self, *, endpoint: str, token: str, model: str, runner: Any = urllib.request.urlopen) -> None:
        if not endpoint.startswith(("http://", "https://")) or not token or not model:
            raise ValueError("AI endpoint, token and model are required")
        self._endpoint = endpoint.rstrip("/")
        self._token = token
        self._model = model
        self._runner = runner

    def suggest(self, *, conversation_id: str, latest_message: InboxMessage) -> str:
        return self.suggest_with_context(conversation_id=conversation_id, latest_message=latest_message, history=[], limit=1)[0]

    def suggest_with_context(
        self, *, conversation_id: str, latest_message: InboxMessage, history: Sequence[dict[str, object]], limit: int
    ) -> list[str]:
        transcript = "\n".join(f"{entry.get('sender', 'contact')}: {entry.get('body', '')}" for entry in history[-20:])
        prompt = (
            "You write concise reply drafts for a human operator. Return only the proposed reply text. "
            f"Conversation {conversation_id}; source={latest_message.source}.\nHistory:\n{transcript}"
        )
        payload = {
            "model": self._model,
            "n": limit,
            "messages": [
                {"role": "system", "content": "Do not claim to send messages. Produce drafts only."},
                {"role": "user", "content": prompt},
            ],
        }
        request = urllib.request.Request(
            self._endpoint + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"},
            method="POST",
        )
        try:
            with self._runner(request, timeout=20) as response:
                if int(response.status) != 200:
                    raise RuntimeError(f"AI provider returned HTTP {response.status}")
                result = json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"AI provider returned HTTP {error.code}") from error
        choices = result.get("choices", []) if isinstance(result, dict) else []
        drafts = [str(choice.get("message", {}).get("content", "")).strip() for choice in choices if isinstance(choice, dict)]
        return [draft for draft in drafts if draft]
