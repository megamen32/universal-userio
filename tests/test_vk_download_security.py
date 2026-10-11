"""Public VK download must not embed operator credentials or require VK ID to reveal URL."""
import json
import re
from pathlib import Path
from zipfile import ZipFile

from universal_userio.http_api import _vk_connect_page


STATIC = Path(__file__).parents[1] / "src" / "universal_userio" / "static"


def test_vk_connect_page_explains_vk_id_and_links_to_installable_archive():
    page = _vk_connect_page("12345")
    assert "vk-userio-extension-mv3.zip" in page
    assert 'href="/vk-userio-extension-mv3.zip" download' in page
    assert "chrome://extensions" in page
    assert "VK ID" in page and "переписки" in page
    assert "личный" in page
    assert "Расширение читает открытую сессию VK" in page


def test_downloadable_archives_never_embed_server_key():
    for name in ("vk-userio-extension.zip", "vk-userio-extension-mv3.zip"):
        with ZipFile(STATIC / name) as archive:
            assert archive.testzip() is None
            config = archive.read("lib/config.js").decode("utf-8")
            match = re.search(r"USERIO_CONFIG\s*=\s*(\{[^;]+\})", config)
            assert match is not None
            data = json.loads(match.group(1))
            assert data.get("token") == ""
            assert data.get("endpoint") == "https://msg.bezrabotnyi.com"
            assert b"Bearer " not in config.encode()
    assert not (STATIC / "vk-userio-extension.crx").exists()
