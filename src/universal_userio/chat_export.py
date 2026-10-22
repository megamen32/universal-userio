"""Whole-chat export for agents: one Markdown file instead of a chat-sized payload.

Reading a chat through the page tools costs one tool result per page and ends
up in the conversation. Exporting it costs one file that the model can open,
grep and quote. The file is written next to the UserIO data root so a model on
the same host reads it directly; the authenticated HTTP route serves it to
everybody else.

Content rules:
- who wrote what: the author name from provider routing metadata, otherwise the
  contact name, otherwise the raw sender id, plus the direction and a local
  timestamp;
- voice: the stored transcript is inlined and explicitly marked as a transcribed
  voice message, so nobody mistakes speech for typed text;
- media: images, video and files are byte-heavy, so they are opt-in. Without
  them the export stays cheap text; with them every attachment gets a marker
  and its authenticated download path, never the bytes themselves;
- forwards: UserIO does not capture the origin of a forwarded message, so the
  export states that instead of pretending the history holds no forwards.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

# UserIO works in Moscow time (МСК, UTC+03:00) and the runtime container runs
# in UTC, so the export states and renders the reference time zone itself.
OWNER_TIMEZONE = timezone(timedelta(hours=3), "МСК")
OWNER_TIMEZONE_LABEL = "МСК (UTC+03:00)"

MAX_MESSAGES_LIMIT = 50000
DEFAULT_MAX_MESSAGES = 20000
INLINE_CONTENT_LIMIT = 32768
PREVIEW_CHARS = 2000
# An export is derived from the store and can be rebuilt, so old copies are
# pruned instead of filling the data volume.
EXPORT_RETENTION_SECONDS = 7 * 24 * 60 * 60

MEDIA_LABELS = {
    "photo": "фото",
    "image": "изображение",
    "video": "видео",
    "audio": "аудио",
    "voice": "голосовое",
    "sticker": "стикер",
    "document": "документ",
    "file": "файл",
    "webpage": "ссылка",
}
# Routing metadata carries the author and is not content; service attachments
# duplicate the body of a system message.
HIDDEN_ATTACHMENT_KINDS = {"telegram_routing", "service"}
# Visual media stays behind the opt-in flag: one line per picture is cheap, a
# chat with thousands of pictures is not.
OPTIONAL_MEDIA_KINDS = {"photo", "image", "video", "sticker", "webpage"}
# Documents and files are metadata only, so they are always named.
ALWAYS_MEDIA_KINDS = {"document", "file"}

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class ExportArgumentsInvalid(ValueError):
    """Caller supplied export arguments outside the bounded contract."""


def _safe(value: str) -> str:
    cleaned = _UNSAFE.sub("_", str(value or "")).strip("._")
    return cleaned[:96] or "export"


def exports_root(store: Any) -> Path:
    """Directory that holds the owner's exported chat files."""
    path = getattr(store, "_path", None)
    base = Path(path).resolve().parent if path else Path("/var/lib/universal-userio")
    return base / "exports"


def user_exports_dir(store: Any, user_id: str | None) -> Path:
    return exports_root(store) / _safe(str(user_id or "owner"))


def _timestamp(value: Any) -> str:
    try:
        moment = datetime.fromtimestamp(float(value), OWNER_TIMEZONE)
    except (TypeError, ValueError, OSError):
        return "?"
    return moment.strftime("%Y-%m-%d %H:%M:%S %z")


def _iso(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value), OWNER_TIMEZONE).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return ""


def _conversation_display_name(store: Any, uid: str, row: Mapping[str, Any]) -> str:
    source = str(row["source"] or "")
    account_ref = str(row["account_ref"] or "")
    peer_id = str(row["peer_id"] or "")
    sender = str(row["sender"] or "")
    key = (f"chat:{account_ref}:{peer_id}"
           if source in {"max", "phone", "vk", "yandex"} and account_ref and peer_id else sender)
    name_row = store._connection.execute(
        "SELECT name,is_explicit FROM contact_names WHERE user_id=? AND source=? AND sender=?",
        (uid, source, key)).fetchone()
    explicit = str(name_row["name"] or "") if name_row and name_row["is_explicit"] else ""
    profile = store._connection.execute(
        "SELECT username,first_name,last_name,phone FROM search_profiles"
        " WHERE user_id=? AND conversation_id=?", (uid, row["id"])).fetchone()
    profile_name = ""
    if profile is not None:
        profile_name = " ".join(
            str(profile[key] or "").strip() for key in ("first_name", "last_name")
            if str(profile[key] or "").strip()
        )
        if not profile_name and str(profile["username"] or "").strip():
            profile_name = "@" + str(profile["username"]).strip().lstrip("@")
    fallback = str(name_row["name"] or "") if name_row else ""
    return explicit or profile_name or fallback or sender or row["id"]


def _sender_names(store: Any, uid: str, source: str) -> dict[str, str]:
    names: dict[str, str] = {}
    explicit: dict[str, str] = {}
    for row in store._connection.execute(
        "SELECT sender,name,is_explicit FROM contact_names WHERE user_id=? AND source=?",
        (uid, source)
    ):
        sender = str(row["sender"] or "")
        if not sender:
            continue
        if row["is_explicit"]:
            explicit[sender] = str(row["name"] or "")
        elif sender not in names:
            names[sender] = str(row["name"] or "")
    names.update(explicit)
    return names


def _routing_metadata(attachment: Mapping[str, Any]) -> dict[str, Any]:
    raw = str(attachment.get("provider_ref") or "")
    if not raw.startswith("{"):
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _media_url(conversation_id: str, message_id: str, index: int) -> str:
    from urllib.parse import quote

    base = (f"/v1/conversations/{quote(conversation_id, safe='')}"
            f"/media/{quote(message_id, safe='')}/raw")
    return f"{base}?attachment_index={index}" if index else base


def _render_message(row: Mapping[str, Any], attachments: Sequence[Mapping[str, Any]],
                    names: Mapping[str, str], conversation_id: str, *,
                    include_images: bool) -> list[str]:
    stamp = _timestamp(row["received_at"])
    sender = str(row["sender"] or "")
    outgoing = str(row["direction"] or "") == "outgoing"
    routing: dict[str, Any] = {}
    reply_to = str(row["provider_reply_to"] or "")
    for attachment in attachments:
        if str(attachment.get("kind") or "") != "telegram_routing":
            continue
        routing = _routing_metadata(attachment)
        reply_to = reply_to or str(routing.get("reply_to_message_id") or "")
        break
    # A group message carries the chat name in the sender column; the routing
    # metadata holds the real author.
    author = str(routing.get("author_name") or "").strip()
    username = str(routing.get("author_username") or "").strip().lstrip("@")
    if not author:
        author = names.get(sender) or sender or "неизвестный"
    if username and username.lower() not in author.lower():
        author = f"{author} (@{username})"
    if outgoing:
        author = f"{author} (владелец)"
    marker = f"## {stamp} · {author} · {'исходящее' if outgoing else 'входящее'}"
    if reply_to:
        marker += f" · ответ на {reply_to}"
    lines = [marker]

    body = str(row["body"] or "").strip()
    voice = [a for a in attachments if str(a.get("kind") or "") in {"voice", "audio"}]
    transcript = ""
    model = ""
    status = ""
    for attachment in voice:
        candidate = str(attachment.get("transcript") or "").strip()
        if candidate:
            transcript = candidate
            model = str(attachment.get("transcription_model") or "")
            status = str(attachment.get("transcription_status") or "")
            break
    if voice:
        caption = body
        if transcript:
            if "[Транскрипция]" in caption:
                caption = caption.split("[Транскрипция]", 1)[0].strip()
            elif caption.strip() == transcript.strip():
                caption = ""
        else:
            caption = body
        if caption:
            lines.append(caption)
        if transcript:
            detail = model or "STT"
            if status:
                detail += f", {status}"
            lines.append(f"[Голосовое сообщение — расшифровка ({detail})]")
            lines.append(transcript)
        else:
            lines.append("[Голосовое сообщение — расшифровки нет]")
    elif body:
        lines.append(body)

    for attachment in attachments:
        kind = str(attachment.get("kind") or "").lower()
        if not kind or kind in HIDDEN_ATTACHMENT_KINDS:
            continue
        if kind in {"voice", "audio"}:
            continue
        optional = kind in OPTIONAL_MEDIA_KINDS
        if optional and not include_images:
            continue
        label = MEDIA_LABELS.get(kind, kind)
        filename = str(attachment.get("filename") or "")
        content_type = str(attachment.get("content_type") or "")
        size = attachment.get("size")
        details = [part for part in (filename, content_type) if part]
        if size:
            details.append(f"{int(size)} Б")
        text = f"[{label}" + (": " + ", ".join(details) if details else "") + "]"
        if include_images or kind in ALWAYS_MEDIA_KINDS:
            index = int(attachment.get("idx") or 0)
            text += f" → {_media_url(conversation_id, str(row['message_id']), index)}"
        lines.append(text)
    return lines


def _window(user_id: str, conversation_id: str, since: float | None, until: float | None,
            prefix: str = "m") -> tuple[str, list[Any]]:
    clauses = [f"{prefix}.user_id=?", f"{prefix}.conversation_id=?", f"{prefix}.deleted_at IS NULL"]
    args: list[Any] = [user_id, conversation_id]
    if since is not None:
        clauses.append(f"{prefix}.received_at>=?")
        args.append(float(since))
    if until is not None:
        clauses.append(f"{prefix}.received_at<=?")
        args.append(float(until))
    return " AND ".join(clauses), args


def export_conversation(store: Any, conversation_id: str, *, user_id: str | None = None,
                        include_images: bool = False, since: float | None = None,
                        until: float | None = None, max_messages: int = DEFAULT_MAX_MESSAGES,
                        write_file: bool = True) -> dict[str, Any]:
    """Render one chat as Markdown and, by default, write it next to the data root."""
    uid = store._user(user_id)
    resolved = store.resolve_conversation_id(str(conversation_id), user_id=uid)
    limit = max(1, min(int(max_messages), MAX_MESSAGES_LIMIT))
    where, args = _window(uid, resolved, since, until)
    with store._lock:
        row = store._connection.execute(
            "SELECT * FROM conversations WHERE user_id=? AND id=?", (uid, resolved)).fetchone()
        if row is None:
            return {}
        conversation = dict(row)
        total = store._connection.execute(
            "SELECT COUNT(*) FROM messages WHERE user_id=? AND conversation_id=? AND deleted_at IS NULL",
            (uid, resolved)).fetchone()[0]
        window_total = store._connection.execute(
            "SELECT COUNT(*) FROM messages m WHERE " + where, args).fetchone()[0]
        messages = store._connection.execute(
            "SELECT m.source,m.message_id,m.sender,m.body,m.direction,m.received_at,m.edited_at,"
            "m.provider_reply_to,m.provider_thread_id FROM messages m WHERE " + where +
            " ORDER BY m.received_at,m.rowid LIMIT ?", (*args, limit)).fetchall()
        attachments = store._connection.execute(
            "SELECT a.source,a.message_id,a.idx,a.kind,a.content_type,a.filename,a.size,"
            "a.attachment_id,a.provider_ref,a.transcript,a.transcription_status,"
            "a.transcription_model FROM message_attachments a JOIN messages m"
            " ON m.user_id=a.user_id AND m.source=a.source AND m.message_id=a.message_id"
            " WHERE " + where + " ORDER BY a.message_id,a.idx", args).fetchall()
        names = _sender_names(store, uid, str(conversation.get("source") or ""))

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for attachment in attachments:
        key = (str(attachment["source"]), str(attachment["message_id"]))
        grouped.setdefault(key, []).append(dict(attachment))

    display_name = _conversation_display_name(store, uid, conversation)
    exported = len(messages)
    voice_count = 0
    voice_without_transcript = 0
    image_count = 0
    document_count = 0
    author_unknown = 0
    lines: list[str] = [
        f"# Чат: {display_name}",
        "",
        f"- conversation_id: {resolved}",
        f"- канал: {conversation.get('source') or ''}"
        + (f" · аккаунт: {conversation.get('account_ref')}" if conversation.get("account_ref") else ""),
        f"- экспортировано: {_iso(time.time())} ({OWNER_TIMEZONE_LABEL})",
        f"- время сообщений: {OWNER_TIMEZONE_LABEL}",
        f"- сообщений в чате: {total}; в окне: {window_total}; в файле: {exported}",
    ]
    header = lines[:]
    body_lines: list[str] = []
    for message in messages:
        key = (str(message["source"]), str(message["message_id"]))
        items = grouped.get(key, [])
        for attachment in items:
            kind = str(attachment.get("kind") or "").lower()
            if kind in {"voice", "audio"}:
                voice_count += 1
                if not str(attachment.get("transcript") or "").strip():
                    voice_without_transcript += 1
            elif kind in OPTIONAL_MEDIA_KINDS:
                image_count += 1
            elif kind in ALWAYS_MEDIA_KINDS:
                document_count += 1
        if not any(str(item.get("kind") or "") == "telegram_routing" for item in items) \
                and str(message["sender"] or "") == display_name:
            # Older group messages carry the chat name instead of the author.
            author_unknown += 1
        body_lines.extend(_render_message(dict(message), items, names, resolved,
                                          include_images=include_images))
        body_lines.append("")

    notes = ["- примечание: UserIO не сохраняет источник пересылок, пересылки в этом файле неразличимы"]
    if author_unknown:
        notes.append(f"- примечание: у {author_unknown} сообщений автор не записан, в поле автора стоит имя чата")
    lines = header + [
        f"- в файле: голосовых сообщений: {voice_count}"
        + (f" (без расшифровки: {voice_without_transcript})" if voice_without_transcript else ""),
        f"- в файле: картинок и видео: {image_count}"
        + (" (не включены)" if not include_images else " (ссылки)"),
        f"- в файле: документов и файлов: {document_count}",
        *notes,
        "",
        "---",
        "",
    ] + body_lines
    markdown = "\n".join(lines).rstrip() + "\n"

    warnings: list[str] = []
    if exported < window_total:
        warnings.append(f"экспортировано {exported} из {window_total} сообщений (лимит {limit})")
    if not include_images and image_count:
        warnings.append("медиафайлы не включены: повторите с include_images=true")
    if voice_without_transcript:
        warnings.append(f"голосовых без расшифровки: {voice_without_transcript}")

    result: dict[str, Any] = {
        "ok": True,
        "conversation": {
            "id": resolved,
            "display_name": display_name,
            "source": conversation.get("source"),
            "account_ref": conversation.get("account_ref"),
            "peer_id": conversation.get("peer_id"),
        },
        "markdown": markdown,
        "stats": {
            "messages_total": total,
            "messages_in_window": window_total,
            "messages_exported": exported,
            "truncated": exported < window_total,
            "voice_messages": voice_count,
            "voice_without_transcript": voice_without_transcript,
            "media_messages": image_count,
            "document_messages": document_count,
            "bytes": len(markdown.encode("utf-8")),
            "lines": markdown.count("\n"),
        },
        "warnings": warnings,
    }
    if write_file:
        name = f"{_safe(resolved)}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.md"
        folder = user_exports_dir(store, uid)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        target.write_text(markdown, encoding="utf-8")
        _prune_exports(folder)
        result["path"] = str(target)
        result["name"] = name
        result["download_url"] = f"/v1/exports/{name}"
    return result


def _prune_exports(folder: Path) -> None:
    cutoff = time.time() - EXPORT_RETENTION_SECONDS
    for candidate in folder.glob("*.md"):
        try:
            if candidate.stat().st_mtime < cutoff:
                candidate.unlink()
        except OSError:
            continue
