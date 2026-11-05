import json
import pytest
from PIL import Image
from content_workbench import hard_subtitles as ocr,media_imports as media,benchmarks
from content_workbench.rapid_ocr_bridge import yellow_mask
from content_workbench.xiaod_bridge import transcript_body,reading_source
from test_app import client,login,ORIGIN

H={'Origin':ORIGIN}

def line(text,x=200,y=480,w=400,h=32,score=.99):
    return {'text':text,'score':score,'words':[{'text':text,'x':x,'y':y,'width':w,'height':h}]}

def test_recurring_subtitle_band_excludes_slide_footer():
    samples=[{'lines':[line('字幕'),line('幻灯片脚注',x=30,y=520,w=800,h=16)]} for _ in range(8)]
    x,y,w,h=ocr.detect_band(samples,922,576)
    assert y<480 and y+h<520
    assert ocr.detect_band([{'lines':[line('脚注',y=520,h=16)]}]*8,922,576) is None

def test_cues_merge_only_contiguous_exact_text_and_preserve_numbers():
    samples=[{'time':i/3,'lines':[line(t)] if t else []} for i,t in enumerate(['收入10元','收入10元','收入100元','','','收入100元'])]
    cues,coverage=ocr.cues_from_samples(samples,3,2)
    assert [c['text'] for c in cues]==['收入10元','收入100元','收入100元']
    assert cues[0]['observations']==2 and coverage==pytest.approx(4/6)
    assert ocr.stamp(61.234)=='00:01:01,234'

def test_srt_numeric_caption_is_not_mistaken_for_sequence_number():
    raw='1\n00:00:01,000 --> 00:00:02,000\n34\n\n2\n00:00:02,000 --> 00:00:03,000\n收入10-20元'
    assert transcript_body(raw)=='34\n\n收入10-20元'


def test_caption_gap_supplement_is_ordered_and_explicitly_unverified(tmp_path):
    (tmp_path/'raw.txt').write_text('原字幕文件',encoding='utf-8')
    (tmp_path/'old.txt').write_text('**00:00:03**: 语音数字可能错\n**00:00:09**: 不应混入',encoding='utf-8')
    (tmp_path/'cues.json').write_text(json.dumps([{'start':0,'end':2,'text':'字幕前'},{'start':6,'end':8,'text':'字幕后'}]),encoding='utf-8')
    source=ocr.with_gaps({'accepted':True,'subtitle':str(tmp_path/'captions.srt'),'duration':8})
    assert source['missing_ranges']==[{'start':2,'end':6}]
    (tmp_path/'text-source.json').write_text(json.dumps({**source,'kind':'hard-subtitles','asr_backup':str(tmp_path/'old.txt')}),encoding='utf-8')
    reading=reading_source(tmp_path)
    assert reading.index('字幕前')<reading.index('字幕缺口')<reading.index('字幕后')
    assert '语音数字可能错' in reading and '不应混入' not in reading
    assert '可能有错字与数字错误' in reading
    assert (tmp_path/'raw.txt').read_text(encoding='utf-8')=='原字幕文件'

def test_color_mask_keeps_yellow_caption_not_black_slide_text():
    image=Image.new('RGB',(4,1));image.putdata([(255,220,20),(20,20,20),(255,255,255),(220,130,130)])
    mask=yellow_mask(image)
    assert [mask.getpixel((x,0)) for x in range(4)]==[255,0,0,0]

def test_manual_ocr_repair_retains_old_raw_and_updates_reading(monkeypatch):
    c=client();login(c);mid=media.new('有字幕视频','视频')
    base=media.folder(mid);old='错误的语音转写';(base/'raw.txt').write_text(old,encoding='utf-8');(base/'source.mp4').write_bytes(b'video')
    media.write(mid,state='done',stage=4,raw=old,article=old,extraction_only=1)
    bid=media.save(mid,{'target':'benchmark'})['id']
    subtitle=base/'verified.srt';subtitle.write_text('1\n00:00:00,000 --> 00:00:02,000\nAI实操',encoding='utf-8')
    monkeypatch.setattr(ocr,'available',lambda:True)
    monkeypatch.setattr(ocr,'extract',lambda *a,**k:{'accepted':True,'subtitle':str(subtitle),'cues':1,'uncertain_cues':0})
    def distill(mid):
        assert media.require(mid)['raw']==subtitle.read_text(encoding='utf-8')
        (base/'clean.txt').write_text('AI实操',encoding='utf-8');(base/'article.md').write_text('AI实操。',encoding='utf-8');(base/'quality.json').write_text('[]',encoding='utf-8')
    monkeypatch.setattr(media,'workbench_distill',distill)
    monkeypatch.setattr(media,'bridge',lambda *a:pytest.fail('OCR must not run ASR'))
    assert c.post(f'/api/media-imports/{mid}/read-subtitles',headers=H,json={}).status_code==200
    media.run({'media_id':mid})
    assert benchmarks.require(bid)['body']=='AI实操。'
    assert media.require(mid)['text_source']['kind']=='hard-subtitles'
    assert next(base.glob('raw-before-ocr-*.txt')).read_text(encoding='utf-8')==old

def test_failed_manual_ocr_does_not_replace_raw_or_call_model(monkeypatch):
    c=client();login(c);mid=media.new('视频','视频');base=media.folder(mid)
    (base/'raw.txt').write_text('旧转写',encoding='utf-8');(base/'source.mp4').write_bytes(b'video')
    media.write(mid,state='done',raw='旧转写',article='旧整理',stage=4)
    monkeypatch.setattr(ocr,'available',lambda:True)
    monkeypatch.setattr(ocr,'extract',lambda *a,**k:{'accepted':False,'reason':'未找到字幕区域'})
    monkeypatch.setattr(media,'workbench_distill',lambda *a:pytest.fail('No charge after failed OCR'))
    assert c.post(f'/api/media-imports/{mid}/read-subtitles',headers=H,json={}).status_code==200
    with pytest.raises(RuntimeError,match='字幕区域'):media.run({'media_id':mid})
    assert (base/'raw.txt').read_text(encoding='utf-8')=='旧转写'
    assert media.require(mid)['article']=='旧整理'

def test_unavailable_ocr_does_not_schedule_or_mutate(monkeypatch):
    c=client();login(c);mid=media.new('音频','音频')
    monkeypatch.setattr(ocr,'available',lambda:False)
    assert c.post(f'/api/media-imports/{mid}/read-subtitles',headers=H,json={}).status_code==400
    assert media.require(mid)['state']=='queued'
