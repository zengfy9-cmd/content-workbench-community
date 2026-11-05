"""Use the owner's existing TikHub credential for public social-video resolution."""
import json
import os
from pathlib import Path
import re
from urllib.parse import urljoin,urlsplit
import httpx
from . import references

def key():return os.environ.get('TIKHUB_API_KEY') or os.environ.get('TIKHUB API') or ''

def walk(value):
    if isinstance(value,dict):
        yield value
        for item in value.values():yield from walk(item)
    elif isinstance(value,list):
        for item in value:yield from walk(item)

def select_note(data,note_id):
    matches=[node for node in walk(data) if any(str(node.get(k,''))==note_id for k in ('id','note_id','noteId')) and (isinstance(node.get('video'),dict) or isinstance(node.get('video_info_v2'),dict))]
    if not matches:raise RuntimeError('TikHub没有返回与原链接匹配的视频笔记；不会用其他笔记替代')
    return matches[0]

def resolve_xhs(url):
    for _ in range(6):
        match=re.search(r'/(?:explore|item)/([0-9a-fA-F]{24})(?:[/?#]|$)',url)
        if match:return url,match.group(1)
        host=urlsplit(url).hostname or ''
        if not any(host==x or host.endswith('.'+x) for x in ('xiaohongshu.com','xhslink.com','xhslink.cn')):break
        references._addresses(host,443 if url.startswith('https:') else 80)
        response=httpx.get(url,follow_redirects=False,timeout=20)
        if not response.is_redirect:break
        url=urljoin(url,response.headers.get('location',''))
    raise RuntimeError('无法确认小红书笔记ID，请粘贴完整笔记链接或上传视频')

def download_file(url,path):
    # No API authorization is forwarded to CDN hosts.
    for _ in range(6):
        url=references.article_url(url);host=urlsplit(url).hostname
        references._addresses(host,443 if url.startswith('https:') else 80)
        with httpx.stream('GET',url,follow_redirects=False,timeout=60) as response:
            if response.is_redirect:
                url=urljoin(url,response.headers['location']);continue
            if response.status_code!=200:raise RuntimeError('视频下载地址不可用，请重新解析或上传文件')
            size=0
            try:
                with path.open('wb') as out:
                    for chunk in response.iter_bytes(1024*1024):
                        size+=len(chunk)
                        if size>250*1024*1024:raise RuntimeError('视频超过250MB，请上传裁剪后的文件')
                        out.write(chunk)
                if not size:raise RuntimeError('视频下载结果为空')
                return
            except Exception:
                path.unlink(missing_ok=True);raise
    raise RuntimeError('视频下载重定向次数过多')

def download(url,platform,folder):
    credential=key()
    if not credential:raise RuntimeError('未找到已有TikHub凭据，可先上传视频或转写稿')
    cache=folder/'tikhub-response.json'
    nid=''
    if platform=='小红书':url,nid=resolve_xhs(url)
    if cache.is_file():data=json.loads(cache.read_text(encoding='utf-8'))
    else:
        endpoint='/api/v1/douyin/app/v3/fetch_one_video_by_share_url' if platform=='抖音' else '/api/v1/xiaohongshu/app_v2/get_video_note_detail'
        params={'share_url':url} if platform=='抖音' else {'share_text':url}
        response=httpx.get('https://api.tikhub.io'+endpoint,params=params,headers={'Authorization':'Bearer '+credential},timeout=60)
        if response.status_code!=200:raise RuntimeError('TikHub解析失败（HTTP '+str(response.status_code)+'），请检查额度或上传文件')
        data=response.json()
        if data.get('code',200)!=200:raise RuntimeError('TikHub未返回成功结果，请检查链接是否仍有效')
        cache.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    if platform=='小红书':
        note=select_note(data,nid);urls=[]
        for node in walk(note.get('video') or note.get('video_info_v2')):
            for name in ('master_url','play_url','url'):
                if isinstance(node.get(name),str) and node[name].startswith(('http://','https://')):urls.append(node[name])
        title=note.get('title') or note.get('desc') or '小红书视频'
        author=(note.get('user') or note.get('user_info') or {}).get('nickname','')
    else:
        candidates=[n for n in walk(data.get('data',{})) if isinstance(n.get('video'),dict) and n.get('aweme_id')]
        if not candidates:raise RuntimeError('TikHub未返回匹配的抖音视频')
        note=candidates[0];play=note['video'].get('play_addr') or note['video'].get('play_addr_h264') or {}
        urls=play.get('url_list') or []
        title=note.get('desc') or '抖音视频';author=(note.get('author') or {}).get('nickname','')
    urls=[u for u in urls if isinstance(u,str) and u.startswith(('http://','https://'))]
    if not urls:raise RuntimeError('未取得视频下载地址，可能是图文笔记、受限或已失效的内容')
    path=folder/'source.mp4'
    download_file(urls[0],path)
    # Only inspect subtitle fields attached to this matched video. Never treat
    # descriptions or unrelated URLs in the response as a transcript.
    video=note.get('video') or note.get('video_info_v2') or {}
    subtitles=video.get('subtitle_infos') or video.get('subtitles') or []
    if isinstance(subtitles,list):
        subtitles=sorted((s for s in subtitles if isinstance(s,dict)),key=lambda s:not str(s.get('language') or s.get('language_code') or '').startswith('zh'))
        for subtitle in subtitles:
            address=subtitle.get('url') or subtitle.get('Url')
            if not isinstance(address,str) or not address.startswith(('https://','http://')):continue
            target=folder/'source.platform.vtt'
            try:
                download_file(address,target)
                content=target.read_text(encoding='utf-8-sig')
                if '-->' not in content:
                    target.unlink(missing_ok=True);continue
                break
            except (httpx.HTTPError,RuntimeError,ValueError,UnicodeError):
                target.unlink(missing_ok=True)
    meta={'title':str(title)[:300],'uploader':str(author)[:120],'webpage_url':url,'resolver':'TikHub'}
    (folder/'download.json').write_text(json.dumps({'audio':path.name,'meta':meta},ensure_ascii=False),encoding='utf-8')
