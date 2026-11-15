import json
import re

import pytest
from content_workbench import benchmarks, media_imports as media, mobile_inbox, reading_metrics
from test_app import ORIGIN, client, login

H={'Origin':ORIGIN}
URL='https://www.douyin.com/video/12345'
LONG=('先选一个工具。跟着案例做出一个作品。' * 22)+'保留123元和 WorkBuddy。'


def capture(monkeypatch):
    c=client();login(c)
    monkeypatch.setattr(media,'queue',lambda mid:None)
    mobile_inbox.ingest({'message_id':'share','msg_type':'text','create_time':'now',
                         'content':URL+'\n我的判断：保存案例'})
    row=benchmarks.list_benchmarks()[0]
    return c,row['id'],row['media_id']


def test_likes_come_from_matching_cached_video_and_never_relabel_play_count(monkeypatch):
    c,bid,mid=capture(monkeypatch)
    monkeypatch.setattr(reading_metrics.httpx,'get',lambda *a,**k:pytest.fail('do not pay for another lookup'))
    cache=media.folder(mid)/'tikhub-response.json'
    def entry(id,stats):return {'aweme_id':id,'video':{},'statistics':stats}
    def store(stats):
        cache.write_text(json.dumps({'data':[entry('99',{'digg_count':999}),entry('12345',stats)]}),encoding='utf-8')
        reading_metrics.backfill_cached_douyin()
        return c.get(f'/api/benchmarks/{bid}').json()
    assert store({'play_count':123})['like_count'] is None
    detail=store({'digg_count':'42','play_count':0})
    assert detail['like_count']==42 and detail['read_count'] is None and detail['like_count_at']==cache.stat().st_mtime
    assert c.get('/api/benchmarks').json()[0]['like_count']==42
    assert c.put(f'/api/benchmarks/{bid}',json={'like_count':18},headers=H).json()['like_count_source']=='手动补录'
    assert store({'digg_count':43})['like_count']==18
    assert c.put(f'/api/benchmarks/{bid}',json={'url':URL+'6'},headers=H).json()['like_count'] is None
    assert store({'digg_count':43})['like_count'] is None


def test_feishu_asr_adds_free_reading_paragraphs_and_preserves_original(monkeypatch):
    c,bid,mid=capture(monkeypatch)
    media.write(mid,stage=3,raw=LONG,auto_xiaod=0)
    base=media.folder(mid);(base/'raw.txt').write_text(LONG,encoding='utf-8')
    monkeypatch.setattr(media,'workbench_distill',lambda *a,**k:pytest.fail('no automatic API calls'))
    media.run({'media_id':mid})
    detail=c.get(f'/api/benchmarks/{bid}').json()
    task=media.require(mid)
    assert detail['reading_kind']=='paragraphs' and len(detail['passages'])>2
    assert re.sub(r'\s','',detail['body'])==re.sub(r'\s','',LONG)
    assert task['raw']==LONG and (base/'raw.txt').read_text(encoding='utf-8')==LONG
    assert detail['mobile_notes'][0]['note']=='保存案例'
    result=c.post(f'/api/media-imports/{mid}/reading-copy',headers=H,json={'body_hash':detail['body_hash'],'article':'## 入门\n\n用 WorkBuddy 练手。'})
    assert result.status_code==200,result.text
    assert result.json()['reading_kind']=='reviewed-copy' and result.json()['raw']==LONG
    assert c.get(f'/api/benchmarks/{bid}').json()['mobile_notes']==detail['mobile_notes']


def test_old_raw_copies_can_reflow_but_cannot_replace_an_edited_benchmark(monkeypatch):
    c,bid,mid=capture(monkeypatch)
    route=f'/api/benchmarks/{bid}'
    original=c.put(route,headers=H,json={'body':LONG}).json()
    media.write(mid,state='done',stage=4,article=LONG,raw=LONG)
    payload={'body_hash':original['body_hash']}
    assert c.post(f'/api/media-imports/{mid}/paragraphs',headers=H,json=payload).status_code==200
    latest=c.get(route).json()
    assert latest['reading_kind']=='paragraphs' and latest['body_hash']!=original['body_hash']
    assert c.put(route,headers=H,json={'body':'我的人工修改'}).status_code==200
    assert c.post(f'/api/media-imports/{mid}/reading-copy',headers=H,json={**payload,'article':'一\n\n二'}).status_code==409
    assert c.post(f'/api/media-imports/{mid}/prepare-reading',headers=H,json={'body_hash':latest['body_hash']}).status_code==409
    assert c.get(route).json()['body']=='我的人工修改'


def test_new_feishu_douyin_auto_uses_xiaod_and_preserves_readable_fallback_on_failure(monkeypatch):
    c,bid,mid=capture(monkeypatch)
    assert media.require(mid)['auto_xiaod']==1
    base=media.folder(mid);(base/'raw.txt').write_text(LONG,encoding='utf-8')
    media.write(mid,stage=3,raw=LONG)
    calls=[]
    def organize(mid):
        calls.append(mid)
        if len(calls)==1:raise RuntimeError('文字模型额度不足')
        (base/'clean.txt').write_text(LONG,encoding='utf-8')
        (base/'article.md').write_text('## 跟案例练习\n\n'+LONG+'\n\n以上保留原话。',encoding='utf-8')
        (base/'quality.json').write_text('[]',encoding='utf-8')
    monkeypatch.setattr(media,'workbench_distill',organize)
    with pytest.raises(RuntimeError,match='额度不足'):media.run({'media_id':mid})
    first=c.get(f'/api/benchmarks/{bid}').json()
    assert (first['media_state'],first['reading_kind'])==('failed','paragraphs')
    assert len(first['passages'])>2 and calls==[mid]
    assert c.post(f'/api/media-imports/{mid}/retry',headers=H,json={'distiller':'xiaod'}).status_code==400
    assert c.post(f'/api/media-imports/{mid}/retry',headers=H,json={'distiller':'workbench'}).status_code==200
    media.run({'media_id':mid}) # explicit retry reuses the checkpoint
    after=c.get(f'/api/benchmarks/{bid}').json()
    assert (after['media_state'],after['reading_kind'],after['extraction_only'])==('done','xiaod',0)
    assert after['body'].startswith('## 跟案例练习') and calls==[mid,mid]
    assert after['mobile_notes']==first['mobile_notes'] and media.require(mid)['raw']==LONG
    assert (base/'raw.txt').read_text(encoding='utf-8')==LONG


def test_auto_xiaod_does_not_apply_to_other_feishu_video_sources(monkeypatch):
    c,_,_=capture(monkeypatch)
    mobile_inbox.ingest({'message_id':'other','msg_type':'text','create_time':'now',
                         'content':'https://www.bilibili.com/video/BV1example'})
    rows=c.get('/api/benchmarks').json()
    item=next(x for x in rows if x['source_platform']=='B站')
    assert media.require(item['media_id'])['auto_xiaod']==0
