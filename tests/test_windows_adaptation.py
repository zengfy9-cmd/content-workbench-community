import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from content_workbench import llm, model_api, transcription, video
from content_workbench.config import Settings
from content_workbench.platform_tools import chinese_font, command
from test_app import client, login, ORIGIN


def test_backend_selection_and_secret_paths_not_exposed():
    c = client()
    login(c)
    h = {"Origin": ORIGIN}
    assert c.put('/api/settings', json={"llm_model": "unknown"}, headers=h).status_code == 400
    assert c.put('/api/settings', json={"feishu_auto_archive": "false"}, headers=h).status_code == 400
    assert c.put('/api/settings', json={"llm_backend": "model-api", "llm_model": "opus",
                                       "feishu_auto_archive": False}, headers=h).status_code == 200
    assert Settings.load().llm_model == "opus"
    assert not Settings.load().feishu_auto_archive
    assert 'model_connector_path' not in c.get('/api/settings').json()


def test_api_passes_exact_model_without_redirect_or_retry(monkeypatch):
    sent = []
    fake = SimpleNamespace(read_credentials=lambda: {}, claude_provider=lambda _: ('https://api.daitu.cc/v1', 'test-key'),
                           MODELS={'opus': 'claude-opus-4-6'}, redact=lambda s, _: s)
    monkeypatch.setattr(model_api, 'connector', lambda _: fake)
    real_client = httpx.Client
    def handler(request):
        sent.append(json.loads(request.content))
        assert request.headers['authorization'] == 'Bearer test-key'
        return httpx.Response(200, json={'content': [{'type': 'text', 'text': '已排版'}], 'stop_reason': 'end_turn'})
    monkeypatch.setattr(model_api.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    assert model_api.run(Settings(llm_model='opus'), '文章', 10) == '已排版'
    assert len(sent) == 1 and sent[0]['model'] == 'claude-opus-4-6'


def test_truncated_response_never_becomes_draft(monkeypatch):
    fake = SimpleNamespace(read_credentials=lambda: {}, claude_provider=lambda _: ('https://api.daitu.cc/v1', 'test-key'),
                           MODELS={'sonnet': 'claude-sonnet-4-6'})
    monkeypatch.setattr(model_api, 'connector', lambda _: fake)
    real_client = httpx.Client
    monkeypatch.setattr(model_api.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={'stop_reason': 'max_tokens'})), **kw))
    with pytest.raises(RuntimeError, match='不完整'):
        model_api.run(Settings(), '文章', 10)


def test_screenshots_are_confined_to_video_staging(private_dir, tmp_path):
    outside = tmp_path / 'img-1.jpg'
    outside.write_bytes(b'private')
    root = Path(private_dir) / 'llm-cwd' / 'video' / 'draft-1'
    root.mkdir(parents=True)
    inside = root / 'img-2.jpg'
    inside.write_bytes(b'public-demo')
    prompt = f'- 编号 1（10×10，文件 {outside}）：\n- 编号 2（10×10，文件 {inside}）：'
    assert len(model_api.content(prompt, True)) == 1  # prose cannot authorize file reads
    blocks = model_api.content(prompt, True, [outside, inside])
    assert len([b for b in blocks if b['type'] == 'image']) == 1
    assert len(model_api.content(prompt, False)) == 1


def test_missing_tool_backend_is_not_silently_ignored():
    Settings(llm_backend='model-api').save()
    with pytest.raises(llm.LlmUnavailable, match='生财工具连接尚未配置'):
        llm.run('搜索案例', tools=llm.SCYS_TOOLS)


@pytest.mark.skipif(os.name != 'nt', reason='Windows host check')
def test_windows_font_and_npm_command():
    from PIL import ImageFont
    for bold in (False, True):
        font, index = chinese_font(bold)
        assert ImageFont.truetype(font, 24, index=index).getlength('中文排版') > 0
    args = command('npx')
    assert args[0].lower().endswith('node.exe')
    assert args[1].endswith('npx-cli.js')
    assert os.environ['PATH'] in video.tool_env()['PATH']


def test_transcription_result_preserves_word_timestamps(monkeypatch, tmp_path):
    word = SimpleNamespace(word='你好', start=0.0, end=0.5)
    segment = SimpleNamespace(text='你好', start=0.0, end=0.5, words=[word])
    monkeypatch.setattr(transcription, 'backend', lambda: 'faster_whisper')
    monkeypatch.setattr(transcription, '_model', lambda: SimpleNamespace(transcribe=lambda *a, **k: (iter([segment]), None)))
    result = transcription.transcribe(tmp_path / 'audio.wav', '口述', True)
    assert result['text'] == '你好'
    assert result['segments'][0]['words'][0]['end'] == 0.5
