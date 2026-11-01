"""Console-free scheduled-task entry point; keep startup failures in a log."""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--powershell', required=True)
    args = parser.parse_args(argv)
    directory = ROOT / 'logs'
    directory.mkdir(exist_ok=True)
    with (directory / 'startup.log').open('ab', buffering=0) as log:
        try:
            result = subprocess.run([args.powershell, '-NoProfile', '-NonInteractive',
                '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'start.ps1')],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return result.returncode
        except OSError:
            log.write(b'Unable to start translator launcher; check the configured PowerShell path.\n')
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
