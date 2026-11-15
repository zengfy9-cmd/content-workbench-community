"""Persistent private instance. Authentication is mandatory before any content API."""
import os
import threading
import time

from .app import create_app
from .config import Settings


def main():
    import uvicorn
    settings=Settings.load()
    if settings.storage_mode!='cloud' or settings.login_mode!='password' or not settings.secure_cookies:
        raise RuntimeError('云端必须配置独立内容库、HTTPS 与账号登录')
    def backups():
        from .cloud_backup import snapshot
        while True:
            try:snapshot()
            except Exception:print('私有备份暂未完成；现有完整备份保留',flush=True)
            time.sleep(300)
    app=create_app(settings)
    from .reading_metrics import backfill_cached_douyin
    backfill_cached_douyin()
    threading.Thread(target=backups,daemon=True,name='cloud-backups').start()
    uvicorn.run(app,host='0.0.0.0',port=int(os.environ.get('CW_PORT','8765')),proxy_headers=False,access_log=False)

if __name__=='__main__':main()
