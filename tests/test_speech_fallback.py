"""ASR fallbacks must not turn a missing transcript into a completed article."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from content_workbench import media_imports as media, transcription, media_native
from test_app import client, login


def result(text, end=80, voiced=85):
    return {'text':text,'segments':[{'text':text,'start':0,'end':end}],
            'voiced_duration':voiced,'duration':90}


def test_repeated_and_short_transcripts_are_not_accepted():
    assert transcription.speech_issues(result('正常开头'+'Ｇ'*60))
    assert transcription.speech_issues(result('这段只有几句。',end=12))
    assert not transcription.speech_issues(result('口播内容需要保留原文和来源。'*6))
    assert not transcription.speech_issues(result('OK',end=2,voiced=2))


def test_second_attempt_is_bounded_and_uses_first_pass_speech_estimate(monkeypatch):
    values=iter([result('短句',end=2),result('补全了后半段的实际内容。'*8)])
    calls=[]
    def transcribe(*a,**k):
        calls.append(k)
        return next(values)
    monkeypatch.setattr(transcription,'transcribe',transcribe)
    text,attempts=transcription.checked_speech(Path('audio.wav'))
    assert len(calls)==2 and calls[0]['vad'] is True and calls[1]['vad'] is False
    assert text and attempts[0]['issues'] and not attempts[1]['issues']
    calls.clear()
    monkeypatch.setattr(transcription,'transcribe',lambda *a,**k:result('Ｇ'*30))
    text,attempts=transcription.checked_speech(Path('audio.wav'))
    assert not text and len(attempts)==2


def test_platform_caption_holes_use_audio_without_making_up_subtitles(monkeypatch):
    monkeypatch.setattr(media.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='90'))
    assert media.subtitle_has_large_gaps('00:00:00.000 --> 00:00:10.000\n短字幕',Path('source.mp4'))
    assert not media.subtitle_has_large_gaps('00:00:00.000 --> 00:01:30.000\n完整时间范围',Path('source.mp4'))


@pytest.mark.parametrize('partial',['none','platform','ocr'])
def test_new_feishu_missing_or_partial_captions_are_transcribed_before_distilling(monkeypatch,partial):
    c=client();login(c)
    mid=media.new('视频','视频',platform='抖音')
    base=media.folder(mid)
    (base/'source.mp4').write_bytes(b'video')
    media.write(mid,stage=1,auto_xiaod=1,extraction_only=0,distiller='workbench')
    if partial=='platform':
        (base/'source.platform.vtt').write_text('00:00:00.000 --> 00:00:02.000\n短字幕',encoding='utf8')
        monkeypatch.setattr(media,'subtitle_has_large_gaps',lambda *a:True)
    def ocr(mid,required=False):
        if partial!='ocr':return False
        (base/'raw.txt').write_text('原OCR字幕',encoding='utf8')
        (base/'text-source.json').write_text(json.dumps({'kind':'hard-subtitles','missing_ranges':[{'start':2,'end':40}]}),encoding='utf8')
        return True
    monkeypatch.setattr(media,'use_hard_subtitles',ocr)
    monkeypatch.setattr(media.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0))
    calls=[]
    def bridge(mid,mode):
        calls.append(mode)
        assert mode=='transcribe-checked'
        (base/'checked-asr.txt').write_text('真实语音内容。'*35,encoding='utf8')
    def distill(mid):
        assert '真实语音内容' in media.require(mid)['raw']
        (base/'clean.txt').write_text('清稿',encoding='utf8')
        (base/'article.md').write_text('## 标题\n\n真实语音内容。',encoding='utf8')
        (base/'quality.json').write_text('[]',encoding='utf8')
    monkeypatch.setattr(media,'bridge',bridge)
    monkeypatch.setattr(media,'workbench_distill',distill)
    media.run({'media_id':mid})
    assert calls==['transcribe-checked']
    assert media.require(mid)['text_source']['kind']=='asr'
    assert media.require(mid)['state']=='done'
    if partial=='ocr':assert (base/'partial-ocr.srt').read_text(encoding='utf8')=='原OCR字幕'


def test_failed_speech_never_calls_text_model(monkeypatch):
    c=client();login(c);mid=media.new('视频','视频',platform='抖音')
    base=media.folder(mid);(base/'audio.wav').write_bytes(b'audio')
    media.write(mid,stage=2,auto_xiaod=1)
    monkeypatch.setattr(media,'bridge',lambda *a:(_ for _ in ()).throw(RuntimeError('两次语音转写仍有明显缺漏')))
    monkeypatch.setattr(media,'workbench_distill',lambda *a:pytest.fail('No paid model after broken ASR'))
    with pytest.raises(RuntimeError,match='明显缺漏'):media.run({'media_id':mid})
    assert media.require(mid)['state']=='failed'
    assert (base/'audio.wav').read_bytes()==b'audio'


def test_portable_cleanup_keeps_real_gap_evidence(monkeypatch,tmp_path):
    (tmp_path/'request.json').write_text('{}')
    (tmp_path/'raw.txt').write_text('原字幕',encoding='utf8')
    monkeypatch.setattr(media_native,'reading_source',lambda path:'字幕\n\n[字幕缺口，未取得可靠文字]')
    monkeypatch.setattr(media_native.sys,'argv',['adapter','clean',str(tmp_path)])
    media_native.main()
    assert '字幕缺口' in (tmp_path/'clean.txt').read_text(encoding='utf8')
