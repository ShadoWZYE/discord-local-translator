"""Resolve the installed CLI at runtime; app bundle paths change on updates."""
import os
from pathlib import Path
import shutil


def resolve_codex(explicit=None, *, local_app_data=None):
    if explicit and Path(explicit).is_file():return Path(explicit)
    # Prefer the signed-in user's current native CLI, not a shell wrapper.
    command=shutil.which('codex.exe' if os.name=='nt' else 'codex')
    if command and Path(command).is_file():return Path(command)
    base=local_app_data if local_app_data is not None else os.environ.get('LOCALAPPDATA')
    if base:
        root=Path(base)/'OpenAI'/'Codex'/'bin'
        try:
            bundles=sorted((p for p in root.glob('*/codex.exe') if p.is_file()),
                           key=lambda p:p.stat().st_mtime,reverse=True)
        except OSError:bundles=[]
        if bundles:return bundles[0]
    return None
