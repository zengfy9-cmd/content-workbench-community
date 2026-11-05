import io
import urllib.error
from types import SimpleNamespace

from content_workbench import launcher_windows


def test_health_check_bypasses_proxy_and_requires_workbench_response(monkeypatch):
    replies = iter([b'{"registered": false}', b'{"message": "other server"}'])

    class Opener:
        def open(self, url, timeout):
            assert url == 'http://localhost:8765/api/auth/state'
            assert timeout == 2
            return io.BytesIO(next(replies))

    def build(handler):
        assert handler.proxies == {}
        return Opener()

    monkeypatch.setattr(launcher_windows.urllib.request, 'build_opener', build)
    assert launcher_windows.ready('http://localhost:8765')
    assert not launcher_windows.ready('http://localhost:8765')


def test_stopped_server_is_not_ready(monkeypatch):
    class Opener:
        def open(self, *args, **kwargs):
            raise urllib.error.URLError('connection refused')

    monkeypatch.setattr(launcher_windows.urllib.request, 'build_opener', lambda _: Opener())
    assert not launcher_windows.ready('http://localhost:8765')


def test_windowless_watchdog_recovers_server_with_logging(monkeypatch, tmp_path):
    from content_workbench import bootstrap_windows

    def configure(profile):
        assert profile == 'default'
        monkeypatch.setenv('CW_DATA_DIR', str(tmp_path))
        return SimpleNamespace(origins=['http://localhost:8765'], port=8765)

    class Lock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bind(self, address):
            assert address == ('127.0.0.1', 18765)

    calls = []

    def spawn(command, **kwargs):
        assert command == [str(tmp_path / 'python.exe'), '-m', 'content_workbench.app', 'serve']
        assert kwargs['stdout'].name == str(tmp_path / 'server.log')
        assert kwargs['stderr'].name == str(tmp_path / 'server-error.log')
        assert kwargs['env']['CW_DATA_DIR'] == str(tmp_path)
        expected_flags = launcher_windows.subprocess.CREATE_NO_WINDOW if launcher_windows.os.name == 'nt' else 0
        assert kwargs['creationflags'] == expected_flags
        calls.append(command)

    replies = iter([False, True])
    monkeypatch.setattr(bootstrap_windows, 'configure', configure)
    monkeypatch.setattr(launcher_windows.socket, 'socket', lambda: Lock())
    monkeypatch.setattr(launcher_windows, 'ready', lambda url: next(replies))
    monkeypatch.setattr(launcher_windows.subprocess, 'Popen', spawn)
    monkeypatch.setattr(launcher_windows.sys, 'executable', str(tmp_path / 'pythonw.exe'))
    monkeypatch.setattr(launcher_windows.sys, 'argv', ['launcher', '--profile', 'default', '--no-browser'])
    monkeypatch.setattr(launcher_windows.webbrowser, 'open', lambda url: (_ for _ in ()).throw(AssertionError('browser opened')))
    launcher_windows.main()
    assert len(calls) == 1
