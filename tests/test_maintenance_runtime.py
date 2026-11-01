from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
from translator_bot.maintenance_runtime import resolve_codex


class RuntimeResolutionTests(unittest.TestCase):
    def test_existing_explicit_path_wins_without_probing(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'codex.exe';path.touch()
            with patch('translator_bot.maintenance_runtime.shutil.which') as which:
                self.assertEqual(resolve_codex(str(path)),path);which.assert_not_called()

    def test_stale_scheduled_path_uses_current_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'codex.exe';path.touch()
            with patch('translator_bot.maintenance_runtime.shutil.which',return_value=str(path)):
                self.assertEqual(resolve_codex(str(path.parent/'removed.exe')),path)

    def test_no_path_falls_back_to_newest_installed_bundle_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'OpenAI/Codex/bin'
            old=root/'old/codex.exe';new=root/'new/codex.exe'
            for path in (old,new):path.parent.mkdir(parents=True);path.touch()
            os.utime(old,(1,1));os.utime(new,(2,2))
            (root/'incomplete').mkdir()
            with patch('translator_bot.maintenance_runtime.shutil.which',return_value=None):
                self.assertEqual(resolve_codex(str(root/'deleted/codex.exe'),local_app_data=folder),new)
                self.assertIsNone(resolve_codex(local_app_data=str(root/'missing')))
