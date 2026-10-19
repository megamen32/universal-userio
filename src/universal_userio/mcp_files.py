"""Bounded MCP delivery for user-requested attachment reads."""
from __future__ import annotations

import base64
import hashlib
import io
import zipfile
import xml.etree.ElementTree as ET

from .contracts import ChannelFile

MAX_MCP_FILE_BYTES = 4 * 1024 * 1024
MAX_MCP_TEXT_CHARS = 64_000
_READABLE_TYPES = {
    "application/pdf", "text/plain", "text/markdown", "text/csv",
    "application/rtf", "text/rtf", "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.oasis.opendocument.text",
}


def _office_text(data: bytes, content_type: str) -> str:
    member = "word/document.xml" if content_type.endswith("wordprocessingml.document") else "content.xml"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info=archive.getinfo(member)
        if info.file_size>2*1024*1024: raise ValueError("document_xml_exceeds_limit")
        xml=archive.read(member)
    root=ET.fromstring(xml)
    paragraphs=[]
    for element in root.iter():
        if element.tag.rsplit("}",1)[-1] in {"p","h"}:
            value="".join(element.itertext()).strip()
            if value:paragraphs.append(value)
    return "\n".join(paragraphs) if paragraphs else " ".join(root.itertext()).strip()


def mcp_attachment(file: ChannelFile) -> tuple[dict, list[dict]]:
    """Return metadata plus text/resource blocks; never pass oversized bytes."""
    data=file.data
    mime=str(file.content_type or "application/octet-stream").split(";",1)[0].lower()
    if not isinstance(data,bytes) or not data or len(data)>MAX_MCP_FILE_BYTES:
        raise ValueError("attachment_empty_or_exceeds_mcp_limit")
    if mime not in _READABLE_TYPES:
        raise ValueError("unsupported_mcp_document_type")
    if mime=="application/pdf" and not data.startswith(b"%PDF-"):
        raise ValueError("invalid_pdf_signature")
    if mime in {"application/rtf","text/rtf"} and not data.lstrip().startswith(b"{\\rtf"):
        raise ValueError("invalid_rtf_signature")
    sha256=hashlib.sha256(data).hexdigest()
    metadata={"filename":str(file.filename)[:256],"content_type":mime,"size":len(data),
              "sha256":sha256,"delivery":"embedded_resource"}
    blocks=[]
    extracted=None
    truncated=False
    if mime.startswith("text/"):
        text=data.decode("utf-8-sig",errors="replace")
        truncated=len(text)>MAX_MCP_TEXT_CHARS
        extracted=text[:MAX_MCP_TEXT_CHARS]
    elif mime in {"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                  "application/vnd.oasis.opendocument.text"}:
        text=_office_text(data,mime)
        truncated=len(text)>MAX_MCP_TEXT_CHARS
        extracted=text[:MAX_MCP_TEXT_CHARS]
    if extracted is not None:
        metadata.update(text=extracted,text_truncated=truncated)
        blocks.append({"type":"text","text":f"Содержимое документа {metadata['filename']} (SHA-256 {sha256}):\n{extracted}"})
    uri="userio://attachment/"+sha256
    blocks.append({"type":"resource","resource":{"uri":uri,"mimeType":mime,
        "blob":base64.b64encode(data).decode("ascii")}})
    return metadata,blocks
