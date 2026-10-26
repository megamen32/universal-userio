"""Small pure projections for Secretary chat results."""

from typing import Any


def secretary_message_text(message: dict[str, Any]) -> str:
    """Project UserIO's canonical body field into the Secretary tool contract."""
    body = message.get("body")
    if body is None:
        body = message.get("text")
    return body if isinstance(body, str) else ""
