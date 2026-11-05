"""Idempotent local startup shared by the desktop shortcut and scheduled task."""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


def ready(url):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + '/api/auth/state', timeout=2) as response:
            return isinstance(json.load(response).get('registered'), bool)
    except (OSError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', default='default', choices=['default', 'secondary'])
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    base = Path(os.environ.get('LOCALAPPDATA') or (Path.home() / '.local/share'))
    os.environ.setdefault('CW_DATA_DIR', str(base / 'ContentWorkbenchCommunity' / args.profile))
    os.environ['PYTHONUTF8'] = '1'
    from .bootstrap_windows import configure
    settings = configure(args.profile)
    url = settings.origins[0].rstrip('/')
    # A loopback socket lock is released by Windows even after an interrupted launch.
    with socket.socket() as lock:
        try:
            lock.bind(('127.0.0.1', settings.port + 10000))
        except OSError:
            pass  # Another launcher owns startup; wait for its health check below.
        else:
            if not ready(url):
                root = Path(__file__).resolve().parents[2]
                data = Path(os.environ['CW_DATA_DIR'])
                # Keep the server's normal logging streams when the watchdog uses pythonw.
                server_python = Path(sys.executable)
                if server_python.name.lower() == 'pythonw.exe':
                    server_python = server_python.with_name('python.exe')
                with (data / 'server.log').open('ab') as out, (data / 'server-error.log').open('ab') as err:
                    subprocess.Popen([str(server_python), '-m', 'content_workbench.app', 'serve'],
                                     cwd=root, env=os.environ.copy(), stdout=out, stderr=err,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        deadline = time.monotonic() + 40
        while not ready(url):
            if time.monotonic() >= deadline:
                raise RuntimeError('工作台启动失败，请查看私有数据目录中的 server-error.log')
            time.sleep(0.5)
    if not args.no_browser:
        webbrowser.open(url)
    print(f'工作台地址：{url}')


if __name__ == '__main__':
    main()
