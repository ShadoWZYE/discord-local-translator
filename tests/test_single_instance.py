from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from translator_bot.single_instance import project_instance


class SingleInstanceTests(unittest.TestCase):
    def test_process_exclusion_and_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'instance.lock'
            code=('import sys; from pathlib import Path; '
                  'from translator_bot.single_instance import project_instance; '
                  'guard=project_instance(Path(sys.argv[1])); guard.__enter__(); guard.__exit__(None,None,None)')
            with project_instance(path):
                child=subprocess.run([sys.executable,'-c',code,str(path)],capture_output=True,text=True)
                self.assertNotEqual(child.returncode,0)
                self.assertIn('AlreadyRunning',child.stderr)
            child=subprocess.run([sys.executable,'-c',code,str(path)],capture_output=True,text=True)
            self.assertEqual(child.returncode,0,child.stderr)

    def test_separate_projects_and_stale_lock_file(self):
        with tempfile.TemporaryDirectory() as directory:
            a,b=Path(directory)/'a.lock',Path(directory)/'b.lock'
            with project_instance(a),project_instance(b):
                pass
            # File remains, but the OS lock does not.
            self.assertTrue(a.exists())
            with project_instance(a):
                pass
