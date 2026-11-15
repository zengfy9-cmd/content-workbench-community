"""Read only the configured owner's Feishu inbox and forward to the cloud library."""
import json
import time
from datetime import datetime,timedelta
import httpx
import base64
import tempfile
from pathlib import Path
from .config import Settings,data_dir
from . import sources, mobile_inbox, images

def cycle():
    settings=Settings.load();file=data_dir()/'mobile-inbox.json'
    if not settings.cloud_url or not settings.feishu_auto_sync or not file.is_file():return
    cfg=json.loads(file.read_text(encoding='utf-8-sig'))
    cursor=data_dir()/'mobile-forward-cursor.json'
    end=datetime.now().astimezone()
    # A new bridge starts from recent messages; server deduplication is by mid.
    start=json.loads(cursor.read_text(encoding='utf-8'))['stamp'] if cursor.is_file() else (end-timedelta(days=3)).isoformat(timespec='seconds')
    args=['im','+chat-messages-list','--as','user','--chat-id',cfg['chat'],'--no-reactions','--order','asc','--start',start,'--end',end.isoformat(timespec='seconds')]
    token=''
    while True:
        page=sources._lark(args+(['--page-token',token] if token else []))
        messages=[m for m in page['messages'] if m.get('sender',{}).get('id')==cfg['owner'] and not m.get('deleted') and m.get('msg_type')!='system']
        for m in messages:
            keys,_=mobile_inbox.image_parts(m)
            if not keys:continue
            m['_inline_images']={}
            with tempfile.TemporaryDirectory(dir=data_dir()) as tmp:
                root=Path(tmp).resolve()
                for resource in keys:
                    r=sources._lark(['im','+messages-resources-download','--as','user','--message-id',m['message_id'],'--file-key',resource,'--type','image','--output','original'],cwd=root)
                    path=Path(r['saved_path']);path=(path if path.is_absolute() else root/path).resolve()
                    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size>images.MAX_UPLOAD:raise RuntimeError('飞书图片下载结果无效，保留游标')
                    m['_inline_images'][resource]=base64.b64encode(path.read_bytes()).decode()
        for offset in range(0,len(messages),100):
            r=httpx.post('http://127.0.0.1:8765/api/mobile-inbox/ingest',headers={'Origin':'http://localhost:8765'},json={'messages':messages[offset:offset+100]},timeout=180,trust_env=False)
            r.raise_for_status()
        if not page.get('has_more'):break
        nxt=page.get('page_token')
        if not nxt or nxt==token:raise RuntimeError('飞书分页中断，未前移游标')
        token=nxt
    cursor.write_text(json.dumps({'stamp':(end-timedelta(minutes=2)).isoformat(timespec='seconds'),'ok_at':end.isoformat()}),encoding='utf-8')
    (data_dir()/'mobile-forward-error.json').unlink(missing_ok=True)

def main():
    while True:
        try:cycle()
        except Exception:
            (data_dir()/'mobile-forward-error.json').write_text(json.dumps({'time':time.time(),'error':'飞书读取或云端接收未成功，保留游标等待重试；没有向群发送消息'}),encoding='utf-8')
        time.sleep(60)

if __name__=='__main__':main()
