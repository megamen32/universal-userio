"""A cold /run must retain the documented private reverse-socket directory."""
from pathlib import Path
import os
import stat
import subprocess
import tempfile
import unittest


class PrimaryRuntimeDirectoryTests(unittest.TestCase):
    def test_tmpfiles_restores_private_parent_after_boot(self):
        config = Path(__file__).parents[1]/'deploy/userio-primary88.tmpfiles.conf'
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root/'etc').mkdir()
            (root/'etc/passwd').write_text(f'root:x:0:0:root:/root:/bin/sh\nroomhacker:x:{os.getuid()}:{os.getgid()}::/:/bin/sh\n')
            (root/'etc/group').write_text(f'root:x:0:\nroomhacker:x:{os.getgid()}:\n')
            parent = root/'run/agentcall/primary88'
            self.assertFalse(parent.exists())
            subprocess.run(['systemd-tmpfiles', '--root='+str(root), '--create', str(config)],
                           check=True, capture_output=True)
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o700)
            self.assertEqual(parent.stat().st_uid, os.getuid())
            # Existing socket/data are never removed by the provisioning rule.
            marker = parent/'control.sock'; marker.write_text('existing IPC')
            subprocess.run(['systemd-tmpfiles', '--root='+str(root), '--create', str(config)],
                           check=True, capture_output=True)
            self.assertEqual(marker.read_text(), 'existing IPC')
