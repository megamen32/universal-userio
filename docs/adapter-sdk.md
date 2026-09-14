# UserIO Adapter SDK

`userio_adapter_sdk` is the stable dependency boundary for provider adapters.

Provider implementations import only DTOs, protocols, capabilities and adapter errors from this package. They must not import UserIO store, service, HTTP, MCP or UI modules. `universal_userio.channels.core` remains a compatibility re-export for older integrations.

Runtime split:

- `userio_adapter_sdk`: stable provider-neutral contracts.
- `universal_userio.channels.*`: provider libraries and low-level sidecar clients.
- `*_ingress.py`: thin processes that normalize provider events and deliver them to canonical UserIO ingress.
- `Universal UserIO`: canonical users/accounts/conversations/messages/files/policies/MCP.
- `NoticePlace`: system/agent notifications only; it is not a human-message reply transport.

Embedding applications use `Omnichannel` as the multi-provider, multi-account
composition root. Register concrete `Channel` adapters once and select them by
`platform` plus `account_id`; provider SDK clients never cross that boundary.

```python
from userio_adapter_sdk import Omnichannel
from universal_userio.channels.sms import AndroidSmsChannel

channels = Omnichannel()
channels.register(AndroidSmsChannel.from_env(), account_id="sales-phone")
await channels.send_message("sms", "+15551234567", "Hello", account_id="sales-phone")
```

A provider exposes exact capabilities from `AdapterCapabilities`. `Omnichannel`
checks the selected account before dispatch, so an unsupported operation raises
`AdapterNotSupported` before provider code is touched. Per-user
`send_enabled` remains an additional UserIO policy gate above adapter
capabilities.

The portable file/contact surface is:

```python
from userio_adapter_sdk import Contact

media = await channels.download("telegram", chat, message_id)
sent = await channels.upload(
    "telegram", chat, b"contents", filename="report.txt", mime_type="text/plain"
)
contact = await channels.get_contact("telegram", "@ada")
contact = await channels.add_contact(
    "telegram", Contact(display_name="Ada Lovelace", phone="+15550001")
)
contact = await channels.edit_contact("telegram", contact.id, display_name="Ada Byron")
await channels.add_contact_to_group("telegram", contact.id, group)
await channels.remove_contact_from_group("telegram", contact.id, group)
await channels.remove_contact("telegram", contact.id)
```

`Contact`, `ChatMessage`, and `DownloadedMedia` are immutable provider-neutral
DTOs. Optional operations are structurally described by `FilePort`,
`ContactPort`, and `GroupContactPort`, so extending the library does not break
messaging-only `ChatPort` adapters. Telegram supports the complete surface. Email supports `download` for
attachments. SMS, VK and the current WhatsApp bridge omit unsupported
capabilities instead of pretending to support them.
