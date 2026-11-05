"""Layout is local, preserves source text, and reaches WeChat's actual payload."""

import hashlib
import html
import json
import re
from pathlib import Path

import pytest

from content_workbench import db, publish
from content_workbench.textutil import wechat_html
from content_workbench.wechat_layout import DEFAULT_THEME, THEME_IDS, layout_info, themes

from test_app import ORIGIN, client, login

H = {"Origin": ORIGIN}
ARTICLE = (
    "## 课程目标\n\n这是**重点**、==高亮==和*补充*。\n\n"
    "> 学完之后解释原因。\n\n"
    "3. 观察现象\n4. 改变条件\n\n- 保留第一项\n- 保留第二项\n\n"
    "![原配图](/api/images/7)\n\n[资料出处](https://example.com/source?a=1&b=2)\n\n"
    "### 验收\n\n| 项目 | 标准 |\n| --- | --- |\n| 事实 | 有出处 |\n\n"
    "```python\nprint('<原样代码>')\n```\n\n<script>alert('只作为文字')</script>"
)


def test_community_theme_manifest_is_intact():
    folder = Path(__file__).parents[1] / "src/content_workbench/layout_themes"
    provenance = json.loads((folder / "source.json").read_text(encoding="utf-8"))
    assert len(provenance["files"]) == 5
    for name, expected in provenance["files"].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected
    assert [item["name"] for item in themes()] == ["清绿", "浅蓝", "灰墨", "暖金", "淡紫"]


@pytest.mark.parametrize("theme", THEME_IDS)
def test_each_theme_preserves_content_and_exports_inline_only(theme):
    output = wechat_html(ARTICLE, theme)
    for text in ["课程目标", "重点", "高亮", "补充", "学完之后解释原因", "观察现象", "改变条件", "保留第一项", "保留第二项", "验收", "有出处"]:
        assert text in output
    assert 'src="/api/images/7"' in output
    assert 'href="https://example.com/source?a=1&amp;b=2"' in output
    assert "print('<原样代码>')" in html.unescape(output)
    assert '<script>' not in output and '&lt;script&gt;' in output
    assert '<mark' not in output and '#fff0a6' in output
    assert '<style' not in output and '<link' not in output and 'style="' in output
    assert not re.search(r"<(?:ol|ul|li)(?:\s|>)", output)
    assert '3.' in output and '4.' in output
    assert 'max-height:' not in output and 'overflow:auto' not in output
    assert 'table-layout:fixed' in output and 'max-width:100%' in output


def test_community_theme_preserves_headings_without_third_party_labels():
    output = wechat_html("# 主标题\n\n## 第一节\n\n正文\n\n## 第二节", DEFAULT_THEME)
    assert "第一节" in output and "第二节" in output
    assert "TITEL" not in output and "SECTION TITLE" not in output
    assert "#28745e" in output.lower()


def test_theme_saved_per_draft_without_rewriting_source():
    c = client(); login(c)
    did = c.post("/api/drafts", json={"platform": "wechat", "title": "自己的文章", "body": ARTICLE}, headers=H).json()["id"]
    other = c.post("/api/drafts", json={"platform": "wechat", "title": "另一篇", "body": "原文"}, headers=H).json()["id"]
    r = c.put(f"/api/drafts/{did}", json={"wechat_theme": DEFAULT_THEME}, headers=H)
    assert r.status_code == 200, r.text
    draft = c.get(f"/api/drafts/{did}").json()
    assert (draft["title"], draft["body"], draft["wechat_theme"]) == ("自己的文章", ARTICLE, DEFAULT_THEME)
    assert c.get(f"/api/drafts/{other}").json()["wechat_theme"] == ""
    # Reopening/reconnecting reads the stored choice, rather than a browser-only preset.
    db.reset_for_tests()
    assert c.get(f"/api/drafts/{did}").json()["wechat_theme"] == DEFAULT_THEME
    assert c.put(f"/api/drafts/{did}", json={"wechat_theme": ""}, headers=H).status_code == 200
    assert c.get(f"/api/drafts/{did}").json()["body"] == ARTICLE


def test_invalid_themes_cannot_load_paths_or_change_drafts():
    c = client(); login(c)
    did = c.post("/api/drafts", json={"platform": "wechat", "body": "保留正文"}, headers=H).json()["id"]
    for invalid in ["../../secrets", "unknown", None, {}, 1]:
        assert c.post("/api/preview", json={"platform": "wechat", "body": "正文", "wechat_theme": invalid}, headers=H).status_code == 400
        assert c.put(f"/api/drafts/{did}", json={"wechat_theme": invalid}, headers=H).status_code == 400
    assert c.get(f"/api/drafts/{did}").json()["body"] == "保留正文"
    xid = c.post("/api/drafts", json={"platform": "x", "body": "推文"}, headers=H).json()["id"]
    assert c.put(f"/api/drafts/{xid}", json={"wechat_theme": DEFAULT_THEME}, headers=H).status_code == 400
    assert client().get("/api/wechat/themes").status_code == 401


def test_preview_and_wechat_payload_use_identical_theme_html(monkeypatch):
    c = client(); login(c)
    body = "## 正文标题\n\n不能改写的 **重点** 和 ==高亮==。"
    did = c.post("/api/drafts", json={"platform": "wechat", "title": "排版验收", "body": body}, headers=H).json()["id"]
    c.put(f"/api/drafts/{did}", json={"wechat_theme": DEFAULT_THEME}, headers=H)
    preview = c.post("/api/preview", json={"platform": "wechat", "title": "排版验收", "body": body, "wechat_theme": DEFAULT_THEME}, headers=H)
    assert preview.status_code == 200, preview.text
    sent = {}
    monkeypatch.setattr(publish, "wx_draft", lambda *args, **kwargs: sent.update(kwargs) or {"media_id": "LOCAL_TEST_ONLY"})
    # No real account, API upload, model or publication is used by this test.
    r = c.post(f"/api/drafts/{did}/publish", json={"confirm": True, "target": "wechat"}, headers=H)
    assert r.status_code == 200, r.text
    assert sent["html"] == preview.json()["html"]
    assert c.get(f"/api/drafts/{did}").json()["body"] == body
    original = c.post("/api/preview", json={"platform": "wechat", "body": body}, headers=H).json()["html"]
    assert original == wechat_html(body) and original != sent["html"]


@pytest.mark.parametrize("theme,expected", [
    ("green-white-clean", "#28745e"),
    ("ink-blue-editorial", "#315b83"),
    ("graphite-dossier", "#475569"),
    ("sand-gold-journal", "#896b35"),
    ("mist-purple-story", "#72578c"),
])
def test_plain_drafts_gain_theme_headers_and_visible_sections_without_rewriting(theme, expected):
    body = "（配图）保留原来的图片说明\n\n上手门槛\n\n实际操作的文字说明，保持原样。\n\n真实使用成本\n\n费用需要逐项核实。"
    output = wechat_html(body, theme, title="我自己的文章")
    assert output.count("我自己的文章") == 1
    assert output.count("<h1 ") == 1
    assert output.count('data-layout-inferred="true"') == 2
    assert expected in output
    assert layout_info(output) == {"has_title": True, "inferred_headings": 2}
    assert "（配图）保留原来的图片说明" in output
    assert "实际操作的文字说明，保持原样。" in output
    assert "费用需要逐项核实。" in output


def test_explicit_headings_win_and_sentences_are_not_guessed_as_headings():
    body = "# 正文已有标题\n\n## 明确的小标题\n\n普通短段落\n\n保留正常叙述。"
    output = wechat_html(body, DEFAULT_THEME, title="独立标题字段")
    assert "独立标题字段" not in output
    assert output.count("正文已有标题") == 1
    assert layout_info(output) == {"has_title": True, "inferred_headings": 0}
    plain = "这是正文。\n\n问题是什么？\n\n短行，仍然是句子\n\n（配图）界面截图\n\n这是没有写完的\n\n第一行\n第二行\n\n保留。"
    assert layout_info(wechat_html(plain, DEFAULT_THEME))["inferred_headings"] == 0
    assert 'data-layout-inferred="true"' not in wechat_html(plain)


def test_rich_editor_hard_breaks_form_sections_without_losing_inline_content():
    body = "\\\n（配图）保留原图说明\\\n\\\n上手门槛\\\n\\\n已有积累能否复用（你的 Skills、项目说明）\\\n\\\n这是**原来的重点**和[原链接](https://example.com)。\n\n真实使用成本\\\n\\\n实际费用按任务核算。"
    output = wechat_html(body, DEFAULT_THEME, title="原文章标题")
    assert layout_info(output) == {"has_title": True, "inferred_headings": 3}
    assert 'href="https://example.com"' in output
    assert "原来的重点" in output and "实际费用按任务核算。" in output
    assert output.index("上手门槛") < output.index("已有积累能否复用") < output.index("原来的重点") < output.index("真实使用成本")
    # A single line break stays inside a paragraph, not an invented new section.
    assert layout_info(wechat_html("一行叙述\\\n另一行说明\n\n保留。", DEFAULT_THEME))["inferred_headings"] == 0


def test_plain_structure_is_preview_only_and_matches_the_publish_payload(monkeypatch):
    c = client(); login(c)
    body = "上手门槛\n\n从实际任务开始。\n\n真实使用成本\n\n按任务核算费用。"
    title = "格式标题 <script>不能执行</script>"
    did = c.post("/api/drafts", json={"platform": "wechat", "title": title, "body": body}, headers=H).json()["id"]
    c.put(f"/api/drafts/{did}", json={"wechat_theme": "sand-gold-journal"}, headers=H)
    preview = c.post("/api/preview", json={"platform": "wechat", "title": title, "body": body, "wechat_theme": "sand-gold-journal"}, headers=H).json()
    assert preview["layout"] == {"has_title": True, "inferred_headings": 2}
    assert "<script>" not in preview["html"]
    sent = {}
    monkeypatch.setattr(publish, "wx_draft", lambda *args, **kwargs: sent.update(kwargs) or {"media_id": "LOCAL_TEST_ONLY"})
    assert c.post(f"/api/drafts/{did}/publish", json={"confirm": True, "target": "wechat"}, headers=H).status_code == 200
    assert sent["html"] == preview["html"]
    draft = c.get(f"/api/drafts/{did}").json()
    assert (draft["title"], draft["body"]) == (title, body)
