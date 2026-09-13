"""Multi-account registry for UserIO-compatible channel adapters."""

from __future__ import annotations

from dataclasses import dataclass

from . import Channel, ChatMessage, ChatRef


def _required(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        raise ValueError(f"{label} must not be blank")
    return normalized


@dataclass(frozen=True, slots=True)
class ChannelBinding:
    """One provider account bound to one async channel adapter."""

    platform: str
    account_id: str
    channel: Channel


class Omnichannel:
    """Provider-neutral, multi-account channel collection.

    Applications keep business logic coupled only to this registry and the
    :class:`Channel` protocol. Provider SDK objects remain inside adapters.
    """

    def __init__(self) -> None:
        self._bindings: dict[tuple[str, str], ChannelBinding] = {}

    def register(
        self, channel: Channel, *, account_id: str = "default", replace: bool = False
    ) -> ChannelBinding:
        platform = _required(getattr(channel, "platform", ""), "channel platform")
        account = _required(account_id, "channel account_id")
        key = (platform, account)
        if key in self._bindings and not replace:
            raise ValueError(f"channel already registered: {platform}:{account}")
        binding = ChannelBinding(platform=platform, account_id=account, channel=channel)
        self._bindings[key] = binding
        return binding

    def unregister(self, platform: str, *, account_id: str = "default") -> None:
        del self._bindings[(_required(platform, "platform"), _required(account_id, "account_id"))]

    def binding(self, platform: str, *, account_id: str | None = None) -> ChannelBinding:
        provider = _required(platform, "platform")
        if account_id is not None:
            key = (provider, _required(account_id, "account_id"))
            try:
                return self._bindings[key]
            except KeyError as exc:
                raise KeyError(f"channel is not registered: {provider}:{key[1]}") from exc
        matches = [binding for key, binding in self._bindings.items() if key[0] == provider]
        if not matches:
            raise KeyError(f"channel is not registered: {provider}")
        if len(matches) > 1:
            raise ValueError(f"account_id is required for multi-account platform: {provider}")
        return matches[0]

    def channel(self, platform: str, *, account_id: str | None = None) -> Channel:
        return self.binding(platform, account_id=account_id).channel

    @property
    def bindings(self) -> tuple[ChannelBinding, ...]:
        return tuple(self._bindings[key] for key in sorted(self._bindings))

    @property
    def platforms(self) -> tuple[str, ...]:
        return tuple(sorted({binding.platform for binding in self._bindings.values()}))

    async def send_message(
        self,
        platform: str,
        chat: ChatRef,
        text: str,
        *,
        account_id: str | None = None,
        reply_to: int | None = None,
    ) -> ChatMessage:
        return await self.channel(platform, account_id=account_id).send_message(
            chat, text, reply_to=reply_to
        )


__all__ = ["ChannelBinding", "Omnichannel"]
