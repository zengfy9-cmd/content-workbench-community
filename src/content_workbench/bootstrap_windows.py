"""Blank local setup. Existing user settings are never overwritten."""
import os
from pathlib import Path
from .config import Settings, data_dir

def configure(profile='default'):
    if profile not in ('default', 'secondary'):
        raise ValueError('Unknown workspace profile')
    if not (data_dir() / 'settings.json').exists():
        port = 8765 if profile == 'default' else 8766
        Settings(port=port, workspace_label='内容工作台',
                 origins=[f'http://localhost:{port}'],
                 allowed_hosts=[f'localhost:{port}', f'127.0.0.1:{port}'],
                 login_mode='local', llm_backend='disabled', feishu_auto_sync=False,
                 feishu_auto_archive=False).save()
    return Settings.load()

