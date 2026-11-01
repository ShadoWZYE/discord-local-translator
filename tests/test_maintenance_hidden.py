import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('hidden_maintenance', Path(__file__).resolve().parents[1]/'scripts'/'maintenance_hidden.py')
HIDDEN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HIDDEN)


class HiddenMaintenanceTests(unittest.TestCase):
    def test_gui_entry_restores_missing_console_streams_and_keeps_logs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'scripts').mkdir()
            (root/'scripts'/'run_maintenance.py').write_text("import sys\nprint('gate stdout')\nprint('gate stderr',file=sys.stderr)\n",encoding='utf-8')
            with patch.object(HIDDEN,'ROOT',root),patch('sys.stdout',None),patch('sys.stderr',None):
                HIDDEN.main()
            output=(root/'data'/'maintenance'/'gate.log').read_text(encoding='utf-8')
            self.assertIn('gate stdout',output)
            self.assertIn('gate stderr',output)

    def test_background_failure_is_logged_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'scripts').mkdir()
            (root/'scripts'/'run_maintenance.py').write_text("raise RuntimeError('synthetic failure')",encoding='utf-8')
            with patch.object(HIDDEN,'ROOT',root):
                with self.assertRaises(SystemExit) as raised:HIDDEN.main()
            self.assertEqual(raised.exception.code,1)
            self.assertIn('synthetic failure',(root/'data'/'maintenance'/'gate.log').read_text(encoding='utf-8'))

    def test_installer_uses_console_free_action_not_hidden_powershell(self):
        source=(Path(__file__).resolve().parents[1]/'scripts'/'install-maintenance-task.ps1').read_text(encoding='utf-8')
        self.assertIn('pythonw.exe',source)
        self.assertIn('maintenance_hidden.py',source)
        self.assertNotIn('Get-Command pwsh',source)
