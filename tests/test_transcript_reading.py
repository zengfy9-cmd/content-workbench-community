import json
import pytest
from content_workbench import db, media_imports as media, benchmarks
from content_workbench.xiaod_bridge import transcript_body
from test_app import client, login, ORIGIN
from test_benchmarks import setup as benchmark_setup, run as analyze_passage
from test_writing_workspace import report

H={'Origin':ORIGIN}
RAW="# Audio Transcription\n\n**Generated:** 2026-10-31\n\n**Language:** zh\n\n## Transcription with Timestamps\n\n**00:00:01:** 第一段真实内容。\n\n## Full Transcription\n\n第一段真实内容。\n第二段保留123元。"

def test_export_has_one_copy_no_metadata_and_keeps_numbers():
    result=transcript_body(RAW)
    assert result=='第一段真实内容。\n\n第二段保留123元。'
    assert transcript_body('2026')=='2026'
    assert transcript_body('**00:01:02**: 真实口播。')=='真实口播。'
    assert transcript_body('1\n00:00:01,000 --> 00:00:02,000\n真实字幕。')=='真实字幕。'

def test_export_metadata_cannot_be_researched(monkeypatch):
    c=client();login(c)
    item=c.post('/api/benchmarks',headers=H,json={'title':'视频','body':RAW}).json()
    assert c.post(f"/api/benchmarks/{item['id']}/analyze",headers=H,json={'format':'research'}).status_code==400
    assert c.post(f"/api/benchmarks/{item['id']}/analyze",headers=H,json={'paragraph_keys':[item['passages'][0]['key']]}).status_code==400
    assert not db.rows('SELECT * FROM jobs')

def test_local_analysis_preserves_full_report_and_its_reviews(monkeypatch):
    c,item=benchmark_setup()
    full=c.post(f"/api/benchmarks/{item['id']}/research/import",headers=H,json={'body_hash':item['body_hash'],'research':report(item)})
    assert full.status_code==200,full.text
    full_id=c.get(f"/api/benchmarks/{item['id']}").json()['analysis']['id']
    result=analyze_passage(c,item['id'],monkeypatch,paragraph_keys=[item['passages'][1]['key']])
    assert result['analysis']['scope']=='selection'
    assert result['full_report']['analysis']['id']==full_id
    assert result['full_report']['analysis']['research']['sections']
    assert result['full_report']['analysis']['metadata']==c.get(f"/api/benchmarks/{item['id']}?analysis_id={full_id}").json()['analysis']['metadata']

def test_subtitle_file_skips_audio_and_asr(monkeypatch):
    c=client();login(c);mid=media.new('字幕视频','视频')
    base=media.folder(mid);(base/'source.mp4').write_bytes(b'example')
    raw='WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n真实字幕。'
    (base/'source.zh.vtt').write_text(raw,encoding='utf-8')
    media.write(mid,extraction_only=1)
    monkeypatch.setattr(media,'bridge',lambda *a:pytest.fail('ASR must not run'))
    monkeypatch.setattr(media.subprocess,'run',lambda *a,**k:pytest.fail('No audio needed'))
    media.run({'media_id':mid});row=media.require(mid)
    assert row['raw']==raw and row['text_source']['kind']=='subtitles'
    assert {1,2}<=set(row['skipped'])

def test_repair_keeps_raw_and_old_analysis_snapshot(monkeypatch):
    c=client();login(c);mid=media.new('视频','视频')
    base=media.folder(mid);(base/'raw.txt').write_text(RAW,encoding='utf-8')
    media.write(mid,state='done',stage=4,raw=RAW,article=RAW,extraction_only=1)
    bid=media.save(mid,{'target':'benchmark'})['id']
    def clean(mid):
        (base/'clean.txt').write_text('真实正文',encoding='utf-8')
        (base/'article.md').write_text('第一段真实内容。\n\n第二段保留123元。',encoding='utf-8')
        (base/'quality.json').write_text('[]',encoding='utf-8')
    monkeypatch.setattr(media,'workbench_distill',clean)
    assert c.post(f'/api/media-imports/{mid}/prepare-reading',headers=H,json={}).status_code==200
    media.run({'media_id':mid})
    assert media.require(mid)['raw']==RAW
    assert benchmarks.require(bid)['body']=='第一段真实内容。\n\n第二段保留123元。'
    assert list(base.glob('before-reading-*.json'))


def test_batch_research_uses_readable_text_and_keeps_raw(monkeypatch):
    from test_intake_batches import setup,create,drain
    c,calls=setup(monkeypatch);cleaned=[]
    upload=c.post('/api/intake-batches/upload',headers=H,files={'file':('转写.txt',RAW.encode(),'text/plain')}).json()
    def distill(mid,route=None):
        assert route['model'];cleaned.append(mid)
        base=media.folder(mid)
        (base/'clean.txt').write_text('第一段真实内容。',encoding='utf-8')
        (base/'article.md').write_text('第一段真实内容。\n\n第二段保留123元。',encoding='utf-8')
    monkeypatch.setattr(media,'workbench_distill',distill)
    b=create(c,[{**upload,'depth':'basic'}]);drain()
    item=c.get(f"/api/intake-batches/{b['id']}").json()['items'][0]
    assert item['state']=='done' and len(cleaned)==1
    assert benchmarks.require(item['benchmark_id'])['body']=='第一段真实内容。\n\n第二段保留123元。'
    assert media.require(item['media_id'])['raw']==RAW


def test_repair_does_not_overwrite_concurrent_source_edit():
    c=client();login(c);mid=media.new('视频','视频')
    item=c.post('/api/benchmarks',headers=H,json={'title':'视频','body':'旧正文'}).json()
    base=media.folder(mid)
    (base/'repair-target.json').write_text(json.dumps({'id':item['id'],'body_hash':item['body_hash']}),encoding='utf-8')
    c.put(f"/api/benchmarks/{item['id']}",headers=H,json={'body':'人工修订'})
    with pytest.raises(RuntimeError,match='未覆盖'):media.finish_reading_repair(mid,'模型整理')
    assert benchmarks.require(item['id'])['body']=='人工修订'


def test_report_schema_repair_once_with_same_route(monkeypatch):
    from content_workbench import jobs
    from content_workbench.config import Settings,data_dir
    c,item=benchmark_setup();calls=[]
    def structured(prompt,**kwargs):
        calls.append(kwargs['route'])
        return {'research':report(item)} if len(calls)==1 else report(item)
    monkeypatch.setattr(benchmarks,'_structured',structured)
    queued=c.post(f"/api/benchmarks/{item['id']}/analyze",headers=H,json={'depth':'deep'}).json()
    job=db.row('SELECT * FROM jobs WHERE id=?',(queued['job_id'],))
    jobs.run_job(Settings(),job['kind'],json.loads(job['payload']))
    result=c.get(f"/api/benchmarks/{item['id']}").json()
    assert result['analysis']['research']['sections']
    assert len(calls)==2 and calls[0]==calls[1]
    assert (data_dir()/'research-validation'/f"job-{job['id']}.json").exists()
