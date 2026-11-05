import json
import asyncio
from pathlib import Path

import httpx
import pytest

from content_workbench import benchmarks, db, deepseek_api, llm, model_api
from content_workbench.config import SecretStore, Settings
from test_app import ORIGIN, client, login


def mock_api(monkeypatch, handler):
    real = httpx.Client
    real_async = httpx.AsyncClient
    monkeypatch.setattr(deepseek_api.httpx, "AsyncClient", lambda **kw: real_async(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(deepseek_api.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def configure():
    Settings(llm_backend="deepseek-api").save()
    SecretStore().set("deepseek_api_key", "test-deepseek-secret")


def test_pause_closes_inflight_deepseek_request(monkeypatch):
    configure()
    closed = []
    async def handler(request):
        try:
            await asyncio.sleep(60)
        finally:
            closed.append(True)
    mock_api(monkeypatch, handler)
    calls = []
    def cancelled():
        calls.append(True)
        return len(calls) > 1
    with pytest.raises(RuntimeError, match='已暂停'):
        deepseek_api.run('分析', 10, 'analyze', cancelled=cancelled)
    assert closed == [True]


def reply(content='{"ok":true}', finish="stop"):
    return httpx.Response(200, json={"choices": [{"finish_reason": finish, "message": {"content": content, "reasoning_content": "private reasoning"}}]})


def test_settings_store_secret_without_echo_or_overwriting_claude(monkeypatch):
    c = client(llm_model="opus")
    login(c)
    headers = {"Origin": ORIGIN}
    assert c.put("/api/settings", headers=headers, json={"llm_backend": "deepseek-api"}).status_code == 200
    r = c.put("/api/settings/secrets", headers=headers, json={"deepseek_api_key": "test-deepseek-secret"})
    assert r.json()["secrets"]["deepseek_api_key"] is True
    data = c.get("/api/settings").json()
    assert data["llm_model"] == "opus" and data["llm_backend"] == "deepseek-api"
    assert data["deepseek_routes"]["classify"]["model"] == "deepseek-flash"
    assert data["deepseek_routes"]["write"]["model"] == "deepseek-v4-pro"
    assert "test-deepseek-secret" not in json.dumps(data) + r.text
    assert c.put("/api/settings/secrets", headers=headers, json={"deepseek_api_key": "bad\nkey"}).status_code == 400
    assert SecretStore().get("deepseek_api_key") == "test-deepseek-secret"
    configure()
    mock_api(monkeypatch, lambda r: httpx.Response(200, json={"data": [{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"}]}))
    checked = c.post("/api/settings/deepseek/check", headers=headers, json={}).json()
    assert checked["ok"] and "实际生成" in checked["detail"]
    assert c.post("/api/settings/deepseek/check", json={}).status_code == 403
    c.put("/api/settings/secrets", headers=headers, json={"deepseek_api_key": ""})
    assert not c.get("/api/settings").json()["secrets"]["deepseek_api_key"]


@pytest.mark.parametrize("task,model", [("classify", "deepseek-flash"), ("analyze", "deepseek-v4-pro"), ("write", "deepseek-v4-pro"), ("vision", "deepseek-flash")])
def test_explicit_routes_use_official_endpoint_and_final_content(monkeypatch, task, model):
    configure()
    seen = []
    def handler(request):
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == "Bearer test-deepseek-secret"
        seen.append(json.loads(request.content))
        return reply()
    mock_api(monkeypatch, handler)
    assert llm.run("只输出 JSON", task=task, json_output=True) == '{"ok":true}'
    assert len(seen) == 1 and seen[0]["model"] == model
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert seen[0]["thinking"]["type"] == ("disabled" if task == "classify" else "enabled")


@pytest.mark.parametrize("code,expected", [(401, "密钥无效"), (402, "余额不足"), (403, "权限"), (429, "频繁"), (500, "异常"), (503, "繁忙"), (302, "异常")])
def test_http_errors_do_not_leak_or_fall_back(monkeypatch, code, expected):
    configure()
    sent = []
    def handler(request):
        sent.append(request)
        return httpx.Response(code, text="test-deepseek-secret private prompt", headers={"Location": "https://other.example"})
    mock_api(monkeypatch, handler)
    monkeypatch.setattr(model_api, "run", lambda *a, **kw: pytest.fail("unexpected fallback"))
    with pytest.raises(llm.LlmUnavailable, match=expected) as exc:
        llm.run("private prompt", task="write")
    assert len(sent) == 1 and "test-deepseek-secret" not in str(exc.value) and "private prompt" not in str(exc.value)


def test_missing_key_and_external_tools_never_send_a_request(monkeypatch):
    Settings(llm_backend="deepseek-api").save()
    mock_api(monkeypatch, lambda r: pytest.fail("unexpected request"))
    with pytest.raises(llm.LlmUnavailable, match="尚未填写"):
        llm.run("文章", task="write")
    assert not llm.status()["ok"]
    with pytest.raises(llm.LlmUnavailable, match="生财工具连接尚未配置"):
        llm.run("搜索", tools=llm.SCYS_TOOLS, task="analyze")


def test_timeout_has_actionable_error(monkeypatch):
    configure()
    def handler(request):
        raise httpx.ReadTimeout("test-deepseek-secret", request=request)
    mock_api(monkeypatch, handler)
    with pytest.raises(llm.LlmUnavailable, match="超时") as exc:
        llm.run("文章", task="write")
    assert "test-deepseek-secret" not in str(exc.value)


@pytest.mark.parametrize("response,expected", [
    (reply('{"body":"半篇', "length"), "不完整"),
    (reply(""), "可用正文"),
    (httpx.Response(200, text="not json"), "格式异常"),
    (httpx.Response(200, json={"choices": []}), "格式异常"),
])
def test_invalid_or_truncated_results_never_return_text(monkeypatch, response, expected):
    configure()
    mock_api(monkeypatch, lambda r: response)
    with pytest.raises(llm.LlmUnavailable, match=expected):
        llm.run("JSON", task="write", json_output=True)


def test_json_repair_keeps_analysis_route(monkeypatch):
    configure()
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        # A user's setting change only affects subsequent tasks, not a repair.
        Settings(llm_backend="model-api").save()
        return reply('{"topic":"ok"}' if len(sent) == 2 else '{"topic":oops}')
    mock_api(monkeypatch, handler)
    assert benchmarks._structured("拆解，输出 JSON") == {"topic": "ok"}
    assert [x["model"] for x in sent] == ["deepseek-v4-pro"] * 2


def test_images_are_explicit_and_confined_to_video_staging(monkeypatch, private_dir, tmp_path):
    configure()
    inside = Path(private_dir) / "llm-cwd/video/draft-1/img-1.jpg"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"public image")
    outside = tmp_path / "private.jpg"
    outside.write_bytes(b"private image")
    sent = []
    mock_api(monkeypatch, lambda r: (sent.append(json.loads(r.content)) or reply()))
    llm.run("截图", task="vision", allow_read=True, image_paths=[inside, outside])
    content = sent[0]["messages"][0]["content"]
    assert len([x for x in content if x["type"] == "image_url"]) == 1
    assert sent[0]["model"] == "deepseek-flash"
    with pytest.raises(llm.LlmUnavailable, match="截图任务"):
        llm.run("截图", task="write", allow_read=True, image_paths=[inside])


def test_failed_analysis_keeps_existing_source_and_version(monkeypatch):
    configure()
    c = client()
    login(c)
    source = c.post("/api/benchmarks", headers={"Origin": ORIGIN}, json={"title": "真实原文", "body": "这是原有段落。", "tags": ["AI实战"]}).json()
    assert "id" in source
    mock_api(monkeypatch, lambda r: reply('{"topic":invalid'))
    started = c.post(f'/api/benchmarks/{source["id"]}/analyze', headers={"Origin": ORIGIN}, json={})
    assert started.status_code == 200
    after = c.get(f'/api/benchmarks/{source["id"]}').json()
    job = db.row("SELECT * FROM jobs WHERE id=?", (after["pending_job_id"],))
    with pytest.raises(ValueError):
        benchmarks.analyze(Settings.load(), db.loads(job["payload"], {}))
    kept = c.get(f'/api/benchmarks/{source["id"]}').json()
    assert kept["body"] == source["body"] and kept["tags"] == ["AI实战"]
    assert kept["versions"] == []


def test_connection_check_requires_both_models(monkeypatch):
    configure()
    sent = []
    def handler(request):
        sent.append(request)
        assert request.method == "GET" and str(request.url).endswith("/models")
        return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}]})
    mock_api(monkeypatch, handler)
    checked = deepseek_api.check_connection()
    assert not checked["ok"] and "deepseek-v4-pro" in checked["detail"] and len(sent) == 1
