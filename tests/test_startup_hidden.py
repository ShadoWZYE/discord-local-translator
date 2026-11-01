from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from scripts import start_translator_hidden as launcher


class StartupLauncherTests(unittest.TestCase):
    def test_hidden_child_exit_status_and_working_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(launcher,'ROOT',Path(directory)), \
                patch.object(launcher.subprocess,'run',return_value=Mock(returncode=17)) as run:
            self.assertEqual(launcher.main(['--powershell','C:/Program Files/PowerShell/pwsh.exe']),17)
            args=run.call_args
            self.assertEqual(args.args[0][0],'C:/Program Files/PowerShell/pwsh.exe')
            self.assertEqual(args.kwargs['cwd'],Path(directory))
            self.assertIn('-NonInteractive',args.args[0])
            self.assertEqual(args.kwargs['creationflags'],getattr(launcher.subprocess,'CREATE_NO_WINDOW',0))

    def test_failed_launch_has_nonzero_exit_and_private_log(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(launcher,'ROOT',Path(directory)), \
                patch.object(launcher.subprocess,'run',side_effect=OSError('unavailable')):
            self.assertEqual(launcher.main(['--powershell','missing.exe']),1)
            self.assertIn('Unable to start', (Path(directory)/'logs/startup.log').read_text())
