"""Local, evidence-preserving hard-caption OCR using an isolated RapidOCR tool.

Frames, OCR boxes, timings and alternate readings stay beside the original video.
No generative correction or runtime dependency install; models are cached locally.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from functools import lru_cache

SCRIPT=Path(__file__).with_name('rapid_ocr_bridge.py')

def interpreter():
    from .config import data_dir
    return Path(os.environ['CW_OCR_PYTHON']) if os.environ.get('CW_OCR_PYTHON') else data_dir().parent/'tools'/'subtitle-ocr'/'Scripts'/'python.exe'


def run_process(args, *, cancelled=lambda:False, timeout=300):
    flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    with subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**flags) as proc:
        started=time.monotonic()
        while True:
            try:
                stdout,stderr=proc.communicate(timeout=.25)
                break
            except subprocess.TimeoutExpired:
                if cancelled() or time.monotonic()-started>timeout:
                    proc.kill();proc.communicate()
                    raise RuntimeError('字幕识别已停止；原件及已完成识别结果保留')
        if proc.returncode:raise RuntimeError('本机字幕识别未完成；请检查视频和独立 OCR 工具')
        return stdout


@lru_cache(maxsize=1)
def available():
    if not interpreter().is_file() or not shutil.which('ffmpeg'):return False
    try:
        result=run_process([str(interpreter()),'-c',"import importlib.util;print(int(all(importlib.util.find_spec(k) for k in ('rapidocr','onnxruntime'))))"],timeout=20)
        return result.strip()==b'1'
    except (RuntimeError,ValueError,OSError):return False


def probe(video):
    raw=run_process(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height:format=duration','-of','json',str(video)],timeout=30)
    data=json.loads(raw);stream=data['streams'][0]
    return int(stream['width']),int(stream['height']),float(data['format']['duration'])


def recognize(entries,folder,name,*,cancelled=lambda:False,timeout=300,palette='normal'):
    manifest=folder/(name+'-manifest.json');output=folder/(name+'-result.jsonl')
    manifest.write_text(json.dumps(entries,ensure_ascii=False),encoding='utf-8')
    run_process([str(interpreter()),str(SCRIPT),str(manifest),str(output),'--palette',palette],cancelled=cancelled,timeout=timeout)
    return [json.loads(line) for line in output.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def bounds(line):
    words=line.get('words') or []
    if not words:return (0,0,0,0)
    left=min(w['x'] for w in words);top=min(w['y'] for w in words)
    return left,top,max(w['x']+w['width'] for w in words)-left,max(w['y']+w['height'] for w in words)-top


def detect_band(samples,width,height):
    """Require a recurring large, central lower-screen text line, not slide text."""
    clusters={}
    for index,sample in enumerate(samples):
        options=[]
        for line in sample['lines']:
            x,y,w,h=bounds(line)
            if .55*height<y<.94*height and h>=.035*height and w>.08*width and abs(x+w/2-width/2)<.30*width:
                options.append((x,y,w,h))
        if not options:continue
        box=max(options,key=lambda b:b[3]);key=round((box[1]+box[3]/2)/height/.05)
        clusters.setdefault(key,[]).append((index,box))
    if not clusters:return None
    group=max(clusters.values(),key=lambda rows:len(rows))
    if len(group)<max(2,len(samples)*.5):return None
    margin=max(3,round(height*.008))
    top=max(0,int(min(box[1] for _,box in group))-margin)
    bottom=min(height,int(max(box[1]+box[3] for _,box in group))+margin)
    return (0,top,width,bottom-top)


def caption_text(lines):
    if not lines:return ''
    candidates=[line for line in lines if line.get('words')]
    if not candidates:return ''
    tallest=max(bounds(line)[3] for line in candidates)
    candidates=[line for line in candidates if bounds(line)[3]>=tallest*.65]
    candidates.sort(key=lambda line:(round(bounds(line)[1]/max(10,tallest*.7)),bounds(line)[0]))
    texts=[]
    for line in candidates:
        text=line['text'].strip()
        # Chinese OCR separates every glyph; preserve spaces within Latin phrases.
        text=re.sub(r'(?<=[\u3400-\u9fff])\s+|\s+(?=[\u3400-\u9fff])','',text)
        text=re.sub(r'(?<=\d)\s+(?=[\d.,%])|(?<=[.,])\s+(?=\d)','',text)
        if text:texts.append(text)
    return ' '.join(texts)


def cues_from_samples(samples,fps,duration):
    cues=[];hits=0
    for sample in samples:
        text=caption_text(sample['lines']);start=float(sample['time'])
        if not text:continue
        hits+=1
        if cues and cues[-1]['text']==text and start-cues[-1]['end']<=1.1/fps:
            cues[-1]['end']=min(duration,start+1/fps);cues[-1]['observations']+=1
        else:cues.append({'start':start,'end':min(duration,start+1/fps),'text':text,'observations':1,'score':min((line.get('score',0) for line in sample['lines']),default=0)})
    return cues,hits/max(1,len(samples))


def stamp(seconds):
    ms=round(seconds*1000);s,ms=divmod(ms,1000);m,s=divmod(s,60);h,m=divmod(m,60)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def with_gaps(result):
    """Expose meaningful no-caption intervals; coverage is not completeness."""
    if not result.get('accepted'):return result
    cues=json.loads(Path(result['subtitle']).with_name('cues.json').read_text(encoding='utf-8'))
    gaps=[];end=0
    for cue in cues:
        if cue['start']-end>=2:gaps.append({'start':end,'end':cue['start']})
        end=max(end,cue['end'])
    if result['duration']-end>=2:gaps.append({'start':end,'end':result['duration']})
    return {**result,'missing_ranges':gaps}


def extract(video:Path,folder:Path,*,cancelled=lambda:False,fps=3):
    if not available():return {'accepted':False,'reason':'本机独立中文 OCR 工具尚未就绪'}
    folder=folder/'rapid-v3'
    folder.mkdir(parents=True,exist_ok=True)
    width,height,duration=probe(video)
    if not duration>0:raise RuntimeError('无法确认视频时长')
    signature={'size':video.stat().st_size,'mtime_ns':video.stat().st_mtime_ns,'fps':fps,'version':3,'engine':'RapidOCR-3.9.2-PP-OCRv6'}
    cached=folder/'result.json'
    if cached.is_file():
        result=json.loads(cached.read_text(encoding='utf-8'))
        if result.get('signature')==signature:return with_gaps(result)
    entries=[]
    for index,fraction in enumerate((.015,.06,.15,.28,.42,.58,.74,.9)):
        t=duration*fraction;path=folder/f'probe-{index}.png'
        run_process(['ffmpeg','-y','-v','error','-ss',str(t),'-i',str(video),'-frames:v','1',str(path)],cancelled=cancelled,timeout=60)
        entries.append({'time':t,'path':str(path.resolve())})
    samples=recognize(entries,folder,'probe',cancelled=cancelled)
    region=detect_band(samples,width,height)
    if not region:
        result={'accepted':False,'reason':'未找到稳定的字幕区域，保留语音转写，不把画面文字冒充字幕','signature':signature}
        cached.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8');return result
    frames=folder/f"frames-{fps}-{signature['size']}-{signature['mtime_ns']}";frames.mkdir(exist_ok=True)
    x,y,w,h=region;scaled=min(1920,w*2)
    from PIL import Image
    from .rapid_ocr_bridge import yellow_mask
    colored=0
    for entry in entries:
        with Image.open(entry['path']) as image:
            mask=yellow_mask(image.crop((x,y,x+w,y+h)))
            if mask.histogram()[255]/(w*h)>.003:colored+=1
    palette='yellow' if colored>=len(entries)*.5 else 'normal'
    # No OCR on the whole slide: only the region verified by probe boxes.
    run_process(['ffmpeg','-y','-v','error','-i',str(video),'-vf',f'fps={fps},crop={w}:{h}:{x}:{y},scale={scaled}:-1','-start_number','0',str(frames/'frame-%07d.png')],cancelled=cancelled,timeout=max(180,duration*2))
    entries=[{'time':int(path.stem.split('-')[-1])/fps,'path':str(path.resolve())} for path in sorted(frames.glob('frame-*.png'))]
    samples=recognize(entries,folder,'captions',cancelled=cancelled,timeout=max(300,duration*4),palette=palette)
    cues,coverage=cues_from_samples(samples,fps,duration)
    accepted=coverage>=.6 and len({cue['text'] for cue in cues})>=min(3,max(1,int(duration/3)))
    subtitle=folder/'captions.srt'
    subtitle.write_text('\n\n'.join(f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for i,c in enumerate(cues,1)),encoding='utf-8')
    (folder/'cues.json').write_text(json.dumps(cues,ensure_ascii=False),encoding='utf-8')
    result={'accepted':accepted,'reason':'' if accepted else '字幕覆盖率不足，保留原转写供核对','signature':signature,'region':region,'palette':palette,'fps':fps,'duration':duration,'coverage':round(coverage,4),'cues':len(cues),'uncertain_cues':sum(c['score']<.90 for c in cues),'subtitle':str(subtitle.resolve()),'timing_note':'时间为抽帧定位，误差至少约一个采样间隔；OCR不是人工校对字幕'}
    cached.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    return with_gaps(result)
