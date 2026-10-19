"""Bounded MCP delivery and document extraction for user-requested media."""
from __future__ import annotations

import base64
import hashlib
from pathlib import PurePath
import subprocess

from .contracts import ChannelFile

MAX_MCP_FILE_BYTES = 4 * 1024 * 1024
MAX_MCP_TEXT_CHARS = 64_000
_TEXT_TYPES = {"text/plain", "text/markdown", "text/html", "text/xml", "application/json", "application/xml"}
_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".json", ".xml", ".html", ".htm",
                  ".log", ".srt", ".vtt", ".ass", ".yaml", ".yml", ".ini", ".conf",
                  ".toml", ".py", ".js", ".ts", ".sh", ".bat", ".sql"}
_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm", "video/x-matroska"}
_DOCUMENT_TYPES = {
    "application/pdf", "application/rtf", "text/rtf", "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text", "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation", "application/epub+zip", "text/csv",
}


def _extract_anydoc(data: bytes, filename: str) -> str:
    import anydoc

    suffix = PurePath(filename).suffix.lower().lstrip(".")
    # AnyDoc detects content signatures. CSV is the supported signature-less
    # format, so provide its extension when Telegram supplied a filename.
    if suffix in {"csv", "tsv"}:
        return str(anydoc.to_markdown_bytes(data, suffix) or "")
    return str(anydoc.to_markdown_bytes(data) or "")


def _ffmpeg_output(args: list[str], data: bytes, *, timeout: int, output_limit: int) -> bytes:
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=timeout, check=False)
    if result.returncode or not result.stdout or len(result.stdout) > output_limit:
        raise ValueError("video_media_unavailable")
    return result.stdout


def _video_blocks(data: bytes, *, include_images: bool) -> tuple[dict, list[dict]]:
    prefix = ["ffprobe", "-v", "error", "-protocol_whitelist", "pipe,crypto,data",
              "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", "-i", "pipe:0"]
    try:
        info = subprocess.run(prefix, input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"frames": [], "frame_count": 0, "transcription_status": "unavailable",
                "media_status": "unavailable", "processing_error": type(error).__name__}, []
    try:
        duration = float(info.stdout.decode("ascii").strip())
    except (ValueError, UnicodeDecodeError):
        raise ValueError("invalid_video_duration") from None
    if info.returncode or not 0 < duration <= 3600:
        raise ValueError("video_duration_out_of_range")

    blocks: list[dict] = []
    frames: list[dict] = []
    for index in (range(4) if include_images else ()):
        # Sample the middle of each quarter, covering the opening through the end.
        at = duration * (index + 0.5) / 4
        command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-protocol_whitelist", "pipe,crypto,data", "-i", "pipe:0", "-ss", f"{at:.3f}",
                   "-frames:v", "1", "-vf", "scale='min(768,iw)':-2", "-f", "image2pipe",
                   "-vcodec", "mjpeg", "-q:v", "7", "pipe:1"]
        try:
            frame = _ffmpeg_output(command, data, timeout=12, output_limit=512 * 1024)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            continue
        frames.append({"time_seconds": round(at, 2), "bytes": len(frame)})
        blocks.append({"type": "image", "data": base64.b64encode(frame).decode("ascii"),
                       "mimeType": "image/jpeg"})
    result = {"frames": frames, "frame_count": len(frames), "duration_seconds": round(duration, 2)}
    try:
        audio = _ffmpeg_output(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-protocol_whitelist", "pipe,crypto,data", "-i", "pipe:0", "-vn", "-ac", "1", "-ar", "16000",
            "-t", "600", "-f", "wav", "pipe:1"], data, timeout=30, output_limit=20 * 1024 * 1024)
        from .messenger_max_media import transcribe
        transcript = transcribe(audio, "audio/wav", filename="video-audio.wav")
        text = str(transcript.get("transcript") or "")
        result["transcription_status"] = "completed" if text else "empty"
        result["transcription_partial"] = duration > 600 or bool(transcript.get("partial"))
        if text:
            heading = "Расшифровка первых 10 минут видео:" if duration > 600 else "Расшифровка видео:"
            blocks.insert(0, {"type": "text", "text": heading + "\n" + text})
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        result["transcription_status"] = "unavailable"
        result["transcription_error"] = type(error).__name__
    return result, blocks


def mcp_attachment(file: ChannelFile, *, include_images: bool = True) -> tuple[dict, list[dict]]:
    """Return extracted text and MCP media blocks without exceeding byte caps."""
    if type(include_images) is not bool:
        raise ValueError("include_images_must_be_boolean")
    data = file.data
    mime = str(file.content_type or "application/octet-stream").split(";", 1)[0].lower()
    filename = str(file.filename or "attachment")[:256]
    if not isinstance(data, bytes) or not data or len(data) > MAX_MCP_FILE_BYTES:
        raise ValueError("attachment_empty_or_exceeds_mcp_limit")

    suffix = PurePath(filename).suffix.lower()
    is_pdf = mime == "application/pdf" or suffix == ".pdf"
    is_rtf = mime in {"application/rtf", "text/rtf"} or suffix == ".rtf"
    if is_pdf and not data.startswith(b"%PDF-"):
        raise ValueError("invalid_pdf_signature")
    if is_rtf and not data.lstrip().startswith(b"{\\rtf"):
        raise ValueError("invalid_rtf_signature")

    is_image = mime in _IMAGE_TYPES
    is_video = mime in _VIDEO_TYPES or suffix in {".mp4", ".mov", ".webm", ".mkv"}
    is_text = mime in _TEXT_TYPES or suffix in _TEXT_SUFFIXES
    is_document = mime in _DOCUMENT_TYPES or suffix in {
        ".pdf", ".doc", ".docx", ".docm", ".xls", ".xlsx", ".xlsm", ".xlsb",
        ".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".ppsm", ".pot", ".odt", ".ods", ".odp",
        ".rtf", ".epub", ".csv", ".tsv",
    }
    if not (is_image or is_video or is_text or is_document):
        raise ValueError("unsupported_mcp_document_type")

    sha256 = hashlib.sha256(data).hexdigest()
    metadata = {"filename": filename, "content_type": mime, "size": len(data),
                "sha256": sha256, "delivery": "embedded_resource"}
    blocks: list[dict] = []

    if is_text:
        extracted = data.decode("utf-8-sig", errors="replace")
        truncated = len(extracted) > MAX_MCP_TEXT_CHARS
        extracted = extracted[:MAX_MCP_TEXT_CHARS]
        metadata.update(text=extracted, text_truncated=truncated, extraction="utf8")
        blocks.append({"type": "text", "text": f"Содержимое {filename} (SHA-256 {sha256}):\n{extracted}"})
    elif is_video:
        video, video_blocks = _video_blocks(data, include_images=include_images)
        metadata.update(extraction="ffmpeg+userio-whisper", **video)
        blocks.extend(video_blocks)
    elif is_document:
        try:
            extracted = _extract_anydoc(data, filename)
            truncated = len(extracted) > MAX_MCP_TEXT_CHARS
            extracted = extracted[:MAX_MCP_TEXT_CHARS]
            metadata.update(text=extracted, text_truncated=truncated, extraction="firecrawl-anydoc")
            if extracted:
                blocks.append({"type": "text", "text": f"Текст документа {filename} (SHA-256 {sha256}):\n{extracted}"})
        except Exception as error:
            # Keep the original file available to a vision-capable MCP client;
            # expose only the converter error class, never a local path.
            metadata.update(extraction="unavailable", extraction_error=type(error).__name__)

    include_binary = not is_image and not is_video or include_images
    if include_binary:
        encoded = base64.b64encode(data).decode("ascii")
        if is_image:
            blocks.append({"type": "image", "data": encoded, "mimeType": mime})
        blocks.append({"type": "resource", "resource": {"uri": "userio://attachment/" + sha256,
            "mimeType": mime, "blob": encoded}})
    else:
        metadata["delivery"] = "text_only"
    return metadata, blocks
