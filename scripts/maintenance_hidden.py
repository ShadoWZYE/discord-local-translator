"""Console-free pythonw entry point, with stdout/stderr retained in the gate log."""
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import runpy
import traceback

ROOT = Path(__file__).resolve().parent.parent


def main():
    directory = ROOT / 'data' / 'maintenance'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'gate.log').open('a', encoding='utf-8', buffering=1) as log:
        with redirect_stdout(log), redirect_stderr(log):
            try:
                runpy.run_path(str(ROOT / 'scripts' / 'run_maintenance.py'), run_name='__main__')
            except SystemExit:
                raise
            except Exception:
                traceback.print_exc()
                raise SystemExit(1) from None


if __name__ == '__main__':
    main()
