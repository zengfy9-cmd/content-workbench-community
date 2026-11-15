from types import SimpleNamespace
import json

import httpx
import pytest

from content_workbench import db, llm, model_api, jobs
from content_workbench.config import Settings
from test_app import ORIGIN, client, login


def test_write_route_is_exact_and_does_not_enable_other_tasks(monkeypatch):
    s = Settings(llm_backend="disabled", writing_model="claude-opus-5-5")
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: s))
    calls = []
    def run(settings, *args):
        calls.append(settings)
        return '{"title":"草稿","body":"有来源的正文"}'
    monkeypatch.setattr(model_api, "run", run)
    assert "草稿" in llm.run("起草", task="write")
    assert calls[0].llm_model == "claude-opus-5-5" and calls[0].llm_backend == "model-api"
    assert s.llm_backend == "disabled"
    with pytest.raises(llm.LlmUnavailable, match="暂停"):
        llm.run("分类", task="classify")
    with pytest.raises(llm.LlmUnavailable, match="暂停"):
        llm.run("总结习惯", task="analyze")


def test_provider_request_and_wrong_response_never_fall_back(monkeypatch):
    module = SimpleNamespace(read_credentials=lambda: {}, claude_provider=lambda f: ("https://api.daitu.cc/v1", "test-key"),
                             MODELS={"opus": "claude-opus-4-6"}, redact=lambda text, f: text)
    monkeypatch.setattr(model_api, "connector", lambda s: module)
    calls = []
    returned = ["claude-opus-5-5"]
    def handler(request):
        calls.append(json.loads(request.content))
        assert request.url.host == "api.daitu.cc"
        return httpx.Response(200, json={"model": returned[0], "stop_reason": "end_turn",
                                        "content": [{"type": "text", "text": "ok"}]})
    real = httpx.Client
    monkeypatch.setattr(model_api.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    settings = Settings(llm_model="claude-opus-5-5")
    assert model_api.run(settings, "test", 10) == "ok"
    assert calls[0]["model"] == "claude-opus-5-5"
    returned[0] = "claude-sonnet-4-6"
    with pytest.raises(RuntimeError, match="不符"):
        model_api.run(settings, "test", 10)
    assert len(calls) == 2


def test_settings_accept_exact_writing_model_without_changing_general_backend():
    c = client(llm_backend="disabled"); login(c)
    h = {"Origin": ORIGIN}
    assert c.put("/api/settings", json={"writing_model": "claude-opus-5-5"}, headers=h).status_code == 200
    saved = c.get("/api/settings").json()
    assert saved["writing_model"] == "claude-opus-5-5" and saved["llm_backend"] == "disabled"
    assert c.put("/api/settings", json={"writing_model": "other"}, headers=h).status_code == 400


def test_legacy_draft_combines_selected_benchmark_and_personal_library(monkeypatch):
    c = client(); login(c); h = {"Origin": ORIGIN}
    mid = c.post("/api/materials", json={"title": "我的录音", "body": "我做课程样片时亲自把演示放到了开头。", "extract": False}, headers=h).json()["id"]
    bid = c.post("/api/benchmarks", json={"title": "选定对标", "body": "对标作者先写冲突再交代过程。", "author": "参考作者"}, headers=h).json()["id"]
    iid = c.post("/api/ideas", json={"title": "课程样片", "benchmark_ids": [bid]}, headers=h).json()["id"]
    with db.tx() as conn:
        did = conn.execute("INSERT INTO drafts(idea_id,platform,created_at,updated_at) VALUES(?,?,?,?)", (iid,"x",db.now(),db.now())).lastrowid
    captured = []
    monkeypatch.setattr(jobs.prompts, "voice_flags", lambda *a: [])
    monkeypatch.setattr(jobs.prompts, "case_search_on", lambda *a: False)
    def run(prompt, **kw):
        captured.append(prompt)
        assert kw["task"] == "write"
        return '{"title":"课程样片","body":"我把演示放到了开头。"}'
    monkeypatch.setattr(jobs.llm, "run", run)
    jobs._draft(Settings(), {"draft_id": did})
    assert "对标作者先写冲突" in captured[0]
    assert "我做课程样片时亲自" in captured[0]
    assert "不逐句照搬" in captured[0]
    assert f"素材 #{mid}" in db.row("SELECT body FROM comments WHERE draft_id=?", (did,))["body"]
