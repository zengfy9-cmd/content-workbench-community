"""Real local HTTP save/upload/restart check using disposable private data. No AI."""
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from PIL import Image

def main():
    with tempfile.TemporaryDirectory(prefix='cw-smoke-') as temporary:
        os.environ['CW_DATA_DIR'] = temporary
        from content_workbench.bootstrap_windows import configure
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        settings = configure()
        settings.port = port
        settings.origins = [f'http://localhost:{port}']
        settings.allowed_hosts = [f'localhost:{port}', f'127.0.0.1:{port}']
        settings.save()
        base = f'http://127.0.0.1:{port}'
        def start():
            runner = '''from content_workbench.app import create_app
from content_workbench.config import Settings
import uvicorn
settings=Settings.load()
app=create_app(settings)
server=uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=settings.port, log_level='error'))
@app.post('/api/__smoke/stop')
def stop():
    server.should_exit=True
    return {'ok':True}
server.run()
'''
            process = subprocess.Popen([sys.executable, '-c', runner],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ.copy())
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError('Server exited during startup')
                    try:
                        if httpx.get(base + '/api/auth/state', trust_env=False).status_code == 200:
                            return process
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                raise RuntimeError('Server startup timed out')
            except BaseException:
                client.post('/api/__smoke/stop').raise_for_status(); process.wait(timeout=15)
                raise
        process = start()
        try:
            with httpx.Client(base_url=base, headers={'Origin':settings.origins[0]}, trust_env=False) as client:
                assert client.get('/').status_code == 200
                assert client.get('/api/drafts').json() == []
                response = client.post('/api/drafts', json={'title':'安装验收临时稿','body':'只用于测试保存和重启读回。','platform':'wechat'})
                response.raise_for_status(); did = response.json()['id']
                image = io.BytesIO(); Image.new('RGB',(12,12),'white').save(image, format='PNG')
                response = client.post(f'/api/drafts/{did}/images', files={'files':('test.png',image.getvalue(),'image/png')})
                response.raise_for_status(); iid = response.json()[0]['id']
                assert client.get(f'/api/drafts/{did}').json()['body'] == '只用于测试保存和重启读回。'
                process.terminate(); process.wait(timeout=10)
                process = start()
                assert client.get(f'/api/drafts/{did}').json()['title'] == '安装验收临时稿'
                assert client.get(f'/api/images/{iid}').content == image.getvalue()
                response = client.delete(f'/api/drafts/{did}'); response.raise_for_status()
                assert client.get('/api/drafts').json() == []
        finally:
            if process.poll() is None:
                httpx.post(base + '/api/__smoke/stop', headers={'Origin':settings.origins[0]}, trust_env=False).raise_for_status()
                process.wait(timeout=15)
        print('PASS: blank database, frontend HTTP, draft save/reload, image upload/readback, process restart, cleanup. No model calls or platform writes.')

if __name__ == '__main__':
    main()
