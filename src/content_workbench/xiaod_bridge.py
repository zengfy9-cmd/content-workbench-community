"""Isolated adapter to the owner's existing XiaoD; never publishes to Feishu."""
import contextlib
import importlib.util
import json
import os
import re
from pathlib import Path
import sys

DEFAULT_PROJECT = Path.home() / 'Documents' / 'New project' / 'xiaod-agent'

def project():
    return Path(os.environ.get('CW_XIAOD_PROJECT') or DEFAULT_PROJECT)

def transcript_body(raw):
    # Scribe exports both a timestamped view and the same full transcript.
    # Feed one copy to XiaoD, while retaining the untouched export as evidence.
    timed='-->' in raw
    exported=bool(re.search(r'(?im)^\s*#\s*Audio Transcription',raw))
    full=re.search(r'(?im)^\s*#{0,6}\s*Full Transcription\s*:?[ \t]*$',raw)
    if full:
        raw=raw[full.end():].strip()
    if timed:
        captions=[];active=False
        lines=raw.splitlines()
        for index,line in enumerate(lines):
            if '-->' in line:active=True;continue
            if not line.strip():active=False;continue
            if not active:continue
            # Also tolerate missing blank separators between VTT cues. Only a
            # number directly preceding a timing line is a cue index.
            if line.strip().isdigit() and index+1<len(lines) and '-->' in lines[index+1]:continue
            caption=re.sub(r'<[^>]+>','',line).strip()
            if caption and (not captions or captions[-1]!=caption):captions.append(caption)
        return '\n\n'.join(captions)
    lines=[]
    for line in raw.splitlines():
        value=line.strip()
        if re.match(r'^(?:#{1,6}\s*)?(?:Audio Transcription|Transcription with Timestamps)\s*$',value,re.I):continue
        if re.match(r'^\**(?:Generated|Language|Speakers):',value,re.I):continue
        if (exported and value=='---') or (timed and (value=='WEBVTT' or '-->' in value or re.fullmatch(r'\d+',value))):continue
        value=re.sub(r'^\s*\**\[?\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?\]?\**\s*:?\s*','',value)
        if value:lines.append(value)
    return '\n\n'.join(lines)


def reading_source(folder):
    raw=(folder/'raw.txt').read_text(encoding='utf-8')
    evidence=folder/'text-source.json'
    source=json.loads(evidence.read_text(encoding='utf-8')) if evidence.is_file() else {}
    gaps=source.get('missing_ranges') or []
    if source.get('kind')!='hard-subtitles':return transcript_body(raw)
    cue_path=Path(source.get('subtitle','')).with_name('cues.json') if source.get('subtitle') else None
    if not cue_path or not cue_path.is_file():return transcript_body(raw)
    cues=json.loads(cue_path.read_text(encoding='utf-8'))
    backup=Path(source['asr_backup']) if source.get('asr_backup') else None
    old=backup.read_text(encoding='utf-8') if backup and backup.is_file() else ''
    matches=re.findall(r'(?m)^\s*\*\*(\d+):(\d+):(\d+)\*\*:\s*(.+)$',old)
    timed=[(int(h)*3600+int(m)*60+int(s),text) for h,m,s,text in matches]
    pieces=[(cue['start'],cue['text']+(f" [OCR低置信度，{int(cue['start'])//60:02}:{int(cue['start'])%60:02}待核对]" if cue.get('score',1)<.9 else '')) for cue in cues]
    for gap in gaps:
        start,end=gap['start'],gap['end']
        label=f'{int(start)//60:02}:{int(start)%60:02}–{int(end)//60:02}:{int(end)%60:02}'
        texts=[text for t,text in timed if start-1<=t<end]
        note=f'[字幕缺口 {label}，需核对原视频'
        note+=('；以下是旧语音转写补充，可能有错字与数字错误：'+' '.join(texts)) if texts else '；未取得可靠文字，不推测补写'
        pieces.append((start,note+']'))
    return '\n\n'.join(text for _,text in sorted(pieces,key=lambda item:item[0]))


def main():
    from dotenv import load_dotenv
    mode, folder = sys.argv[1:3]
    folder = Path(folder).resolve()
    src = project() / 'scripts' / 'xiaod.py'
    if not src.is_file():
        raise RuntimeError('未找到本机小D项目，请在设置中检查接入路径')
    spec = importlib.util.spec_from_file_location('local_xiaod', src)
    xiaod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(xiaod)
    # Credentials stay inside this child process and the owner's original file.
    load_dotenv(project() / '.env', override=True)
    request = json.loads((folder / 'request.json').read_text(encoding='utf-8')) if (folder/'request.json').is_file() else {}
    with (folder / (mode + '.log')).open('w', encoding='utf-8') as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        if mode == 'status':
            cfg=xiaod.read_hermes_model_config()
            model=os.getenv('XIAOD_LLM_MODEL') or cfg.get('default') or ''
            configured=bool(model and (os.getenv('XIAOD_LLM_API_KEY') or cfg.get('api_key')) and (os.getenv('XIAOD_LLM_BASE_URL') or os.getenv('XIAOD_LLM_URL') or cfg.get('base_url')))
            (folder/'status.json').write_text(json.dumps({'model':model,'configured':configured,'scribe':(xiaod.SCRIBE_DIR/'main.py').is_file()}),encoding='utf-8')
        elif mode == 'download':
            import yt_dlp
            original=yt_dlp.YoutubeDL
            class BoundedDownloader(original):
                def __init__(self,params=None,*args,**kwargs):
                    params=dict(params or {});params.update(max_filesize=250*1024*1024,socket_timeout=25,retries=2)
                    super().__init__(params,*args,**kwargs)
            yt_dlp.YoutubeDL=BoundedDownloader
            audio, meta = xiaod.download_audio(request['url'], folder)
            (folder / 'download.json').write_text(json.dumps({'audio':audio.name,'meta':meta}, ensure_ascii=False), encoding='utf-8')
        elif mode == 'transcribe':
            asr=request.get('asr_model','base')
            cache=Path.home()/'.cache/huggingface/hub'/('models--Systran--faster-whisper-'+asr)
            if any(cache.glob('snapshots/*/model.bin')):os.environ['HF_HUB_OFFLINE']='1'
            path = xiaod.transcribe(folder / 'audio.wav', folder, request.get('asr_model') or os.getenv('XIAOD_MODEL_SIZE', 'base'), os.getenv('XIAOD_LANGUAGE', 'zh'), os.getenv('XIAOD_DEVICE', 'auto'))
            (folder / 'raw.txt').write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
        elif mode == 'quality':
            issues=xiaod.quality_check((folder/'article.md').read_text(encoding='utf-8'))
            (folder/'quality.json').write_text(json.dumps(issues,ensure_ascii=False),encoding='utf-8')
        elif mode == 'clean':
            clean=xiaod.clean_transcript(reading_source(folder))
            (folder/'clean.txt').write_text(clean,encoding='utf-8')
        elif mode == 'distill':
            raw = (folder / 'raw.txt').read_text(encoding='utf-8')
            clean = xiaod.clean_transcript(reading_source(folder))
            (folder / 'clean.txt').write_text(clean, encoding='utf-8')
            pieces=[]
            remaining=clean
            while remaining:
                cut=min(len(remaining),10000)
                if cut<len(remaining):
                    split=remaining.rfind('\n',0,cut)
                    if split>5000:cut=split
                pieces.append(remaining[:cut]);remaining=remaining[cut:]
            outputs=[]
            for i,piece in enumerate(pieces):
                cache=folder/('distilled-'+xiaod.sha_short(piece)+'.md')
                if cache.is_file():article=cache.read_text(encoding='utf-8')
                else:
                    article=xiaod.call_distiller_llm(piece,request['title']+(f'（第{i+1}/{len(pieces)}部分）' if len(pieces)>1 else ''))
                    if not article or (len(piece)>2000 and len(article)<len(piece)*.25):
                        raise RuntimeError('小D未返回完整整理正文；原始转写与已完成部分保留')
                    cache.write_text(article,encoding='utf-8')
                outputs.append(article)
            article='\n\n'.join(outputs)
            cfg=xiaod.read_hermes_model_config()
            (folder/'model.json').write_text(json.dumps({'model':os.getenv('XIAOD_LLM_MODEL') or cfg.get('default') or '', 'parts':len(pieces)}),encoding='utf-8')
            (folder / 'article.md').write_text(article, encoding='utf-8')
            (folder / 'quality.json').write_text(json.dumps(xiaod.quality_check(article), ensure_ascii=False), encoding='utf-8')
        else:
            raise RuntimeError('Unknown XiaoD adapter action')

if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Never put remote response bodies, credentials or signed URLs in API errors.
        print('小D处理失败；请检查本机依赖、平台访问情况及小D模型配置。已完成的文件仍保留。', file=sys.stderr)
        sys.exit(1)
