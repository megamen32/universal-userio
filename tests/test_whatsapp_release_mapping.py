"""The stock release preflight must cover both halves of the WhatsApp adapter."""
import pytest
from scripts.container_release import BACKEND_SOURCE_HASH_PATHS, backend_source_target


def test_whatsapp_native_source_hashes_are_mandatory():
    paths={'src/universal_userio/channels/whatsapp.py':
           '/opt/universal-userio/src/universal_userio/channels/whatsapp.py',
           'deploy/whatsapp-qr-connector/server.mjs':
           '/opt/userio-whatsapp-qr/server.mjs',
           'deploy/whatsapp-qr-connector/whatsapp-send-recovery.mjs':
           '/opt/userio-whatsapp-qr/whatsapp-send-recovery.mjs'}
    for name,destination in paths.items():
        assert name in BACKEND_SOURCE_HASH_PATHS
        assert backend_source_target(name)==destination


def test_unrecognized_paths_cannot_map_to_arbitrary_container_files():
    with pytest.raises(ValueError):
        backend_source_target('../../etc/passwd')
