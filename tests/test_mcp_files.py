import base64
import io
import zipfile

import pytest

from universal_userio.contracts import ChannelFile
from universal_userio.mcp_files import MAX_MCP_FILE_BYTES, mcp_attachment


def test_mcp_text_attachment_has_extracted_text_and_embedded_resource():
    file=ChannelFile("notes.txt","text/plain",b"hello secretary")
    metadata,blocks=mcp_attachment(file)
    assert metadata["text"]=="hello secretary"
    assert blocks[0]["type"]=="text" and "hello secretary" in blocks[0]["text"]
    assert blocks[1]["type"]=="resource"
    assert base64.b64decode(blocks[1]["resource"]["blob"])==file.data
    assert blocks[1]["resource"]["mimeType"]=="text/plain"


def test_mcp_pdf_is_embedded_as_bounded_resource():
    file=ChannelFile("report.pdf","application/pdf",b"%PDF-1.7\nsource")
    metadata,blocks=mcp_attachment(file)
    assert metadata["delivery"]=="embedded_resource"
    assert len(blocks)==1 and blocks[0]["type"]=="resource"
    assert base64.b64decode(blocks[0]["resource"]["blob"])==file.data


def test_docx_extraction_is_bounded_and_typed():
    output=io.BytesIO()
    with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml","<document><p>Hello <t>world</t></p></document>")
    metadata,blocks=mcp_attachment(ChannelFile("contract.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",output.getvalue()))
    assert "Hello world" in metadata["text"]
    assert any(block["type"]=="resource" for block in blocks)


@pytest.mark.parametrize("file",[
    ChannelFile("large.pdf","application/pdf",b"x"*(MAX_MCP_FILE_BYTES+1)),
    ChannelFile("script.exe","application/octet-stream",b"MZ"),
])
def test_mcp_resource_rejects_oversized_and_non_document_bytes(file):
    with pytest.raises(ValueError):mcp_attachment(file)
