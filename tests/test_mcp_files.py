import base64

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


def test_csv_uses_anydoc_markdown_extraction():
    metadata,blocks=mcp_attachment(ChannelFile("contacts.csv","text/csv",b"Name,Role\nAda,Engineer\n"))
    assert metadata["extraction"]=="firecrawl-anydoc"
    assert "Ada" in metadata["text"] and "Engineer" in metadata["text"]
    assert any(block["type"]=="resource" for block in blocks)


def test_image_opt_out_omits_all_image_bytes():
    image=ChannelFile("photo.jpg","image/jpeg",b"synthetic-image")
    metadata,blocks=mcp_attachment(image,include_images=False)
    assert metadata["delivery"]=="text_only"
    assert blocks==[]


def test_video_frames_and_transcript_respect_image_opt_out(monkeypatch):
    from universal_userio import mcp_files
    monkeypatch.setattr(mcp_files,"_video_blocks",lambda data,include_images: (
        {"frame_count":int(include_images),"frames":[{"time_seconds":1.0}] if include_images else [],"duration_seconds":4.0,
         "transcription_status":"completed"},
        [{"type":"image","data":"ZmFrZQ==","mimeType":"image/jpeg"}] if include_images else
        [{"type":"text","text":"Расшифровка видео: привет"}],
    ))
    video=ChannelFile("clip.mp4","video/mp4",b"synthetic-video")
    metadata,blocks=mcp_attachment(video,include_images=False)
    assert metadata["transcription_status"]=="completed"
    assert metadata["frame_count"]==0
    assert metadata["delivery"]=="text_only"
    assert [block["type"] for block in blocks]==["text"]


def test_video_extracts_four_evenly_spaced_frames_and_uses_existing_transcriber(monkeypatch):
    from types import SimpleNamespace
    from universal_userio import mcp_files
    import universal_userio.messenger_max_media as media
    monkeypatch.setattr(mcp_files.subprocess,"run",lambda *args,**kwargs:
        SimpleNamespace(returncode=0,stdout=b"8.0"))
    commands=[]
    def ffmpeg(args,data,*,timeout,output_limit):
        commands.append(args)
        return b"synthetic-frame" if "mjpeg" in args else b"synthetic-audio"
    monkeypatch.setattr(mcp_files,"_ffmpeg_output",ffmpeg)
    monkeypatch.setattr(media,"transcribe",lambda data,mime,*,filename:
        {"transcript":"recognized words","partial":False})
    metadata,blocks=mcp_files._video_blocks(b"synthetic-video",include_images=True)
    assert [frame["time_seconds"] for frame in metadata["frames"]]==[1.0,3.0,5.0,7.0]
    assert metadata["transcription_status"]=="completed"
    assert metadata["transcription_partial"] is False
    assert blocks[0]["type"]=="text" and "recognized words" in blocks[0]["text"]
    assert sum(block["type"]=="image" for block in blocks)==4
    assert len(commands)==5


def test_video_stays_downloadable_when_ffprobe_is_unavailable(monkeypatch):
    from universal_userio import mcp_files
    def missing(*args, **kwargs):
        raise FileNotFoundError("ffprobe")
    monkeypatch.setattr(mcp_files.subprocess, "run", missing)
    metadata, blocks = mcp_attachment(ChannelFile("clip.mp4", "video/mp4", b"synthetic-video"))
    assert metadata["media_status"] == "unavailable"
    assert metadata["transcription_status"] == "unavailable"
    assert metadata["delivery"] == "embedded_resource"
    assert len(blocks) == 1 and blocks[0]["type"] == "resource"


@pytest.mark.parametrize("file",[
    ChannelFile("large.pdf","application/pdf",b"x"*(MAX_MCP_FILE_BYTES+1)),
    ChannelFile("script.exe","application/octet-stream",b"MZ"),
])
def test_mcp_resource_rejects_oversized_and_non_document_bytes(file):
    with pytest.raises(ValueError):mcp_attachment(file)
