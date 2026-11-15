"""Automatic account presentation through preview, copy source and WeChat payload."""

import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from content_workbench import db, images, publish, wechat_branding
from content_workbench.config import Settings
from content_workbench.wechat_layout import THEME_IDS, _Fragment
from test_app import ORIGIN, client, login

H = {"Origin": ORIGIN}
CTA = "如果这篇有帮助，欢迎点赞、在看、转发，我们下篇见。"


def configured(first=8):
    data = io.BytesIO()
    Image.new("RGB", (240, 90), "#163d3a").save(data, "PNG")
    card = images.save(None, "测试名片", data.getvalue())
    return {"enabled": True, "name": "示例作者", "card_image_id": card["id"],
            "cta": CTA, "first_number": first, "legacy_footer_suffixes": [":::center\n旧落款\n:::"]}


def draft(c, body="原正文。", platform="wechat"):
    r = c.post("/api/drafts", json={"platform": platform, "title": "自己的文章", "body": body}, headers=H)
    assert r.status_code == 200
    return r.json()["id"]


def preview(c, did, body="原正文。", theme=THEME_IDS[0], platform="wechat"):
    r = c.post("/api/preview", json={"platform": platform, "draft_id": did, "title": "自己的文章",
                                    "body": body, "wechat_theme": theme}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("theme", THEME_IDS)
def test_branding_is_centered_ordered_and_not_saved_in_body(theme):
    config = configured()
    c = client(wechat_branding=config); login(c)
    body = "原正文。\n\n:::center\n旧落款\n:::"
    did = draft(c, body)
    output = preview(c, did, body, theme)
    parsed = _Fragment(output["html"]).root
    opening = parsed.find(".//*[@data-workbench-brand='opening']")
    footer = parsed.find(".//*[@data-workbench-brand='footer']")
    assert opening.text == "这是「示例作者」的第 8 篇 AI 原创。"
    assert [node.tag for node in footer] == ["p", "img", "p", "p", "p", "p"]
    assert [node.text for node in footer] == [CTA, None, "点赞", "在看", "转发", "END"]
    assert "text-align:center" in footer[0].attrib["style"]
    assert "旧落款" not in output["html"] and "原正文。" in output["html"]
    assert output["html"].count('data-workbench-brand="footer"') == 1
    assert output["html"].index("自己的文章") < output["html"].index("这是「示例作者」") < output["html"].index("原正文。")
    assert c.get(f"/api/drafts/{did}").json()["body"] == body


def test_number_survives_save_theme_switch_restart_and_deleted_drafts():
    config = configured()
    c = client(wechat_branding=config); login(c)
    did = draft(c)
    for theme in THEME_IDS:
        assert preview(c, did, theme=theme)["branding"]["number"] == 8
        c.put(f"/api/drafts/{did}", json={"wechat_theme": theme}, headers=H).raise_for_status()
    db.reset_for_tests()
    assert preview(c, did)["branding"]["number"] == 8
    assert preview(c, draft(c))["branding"]["number"] == 9
    c.delete(f"/api/drafts/{did}", headers=H).raise_for_status()
    assert preview(c, draft(c))["branding"]["number"] == 10
    assert c.delete(f'/api/images/{config["card_image_id"]}', headers=H).status_code == 400


def test_concurrent_number_requests_reserve_once_and_do_not_collide():
    config = configured()
    settings = Settings(wechat_branding=config)
    c = client(wechat_branding=config); login(c)
    ids = [draft(c) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        numbers = list(pool.map(lambda did: wechat_branding.number_for(settings, did), ids * 4))
    assert len(set(numbers)) == 4 and set(numbers) == set(range(8, 12))
    assert len(db.rows("SELECT * FROM wechat_brand_numbers")) == 4


def test_only_configured_wechat_theme_gets_branding_and_never_erases_other_text():
    config = configured()
    c = client(wechat_branding=config); login(c)
    did = draft(c)
    assert preview(c, did, theme="")["branding"] is None
    assert preview(c, draft(c, platform="x"), platform="x").get("branding") is None
    assert preview(c, draft(c, platform="xiaohongshu"), platform="xiaohongshu")["branding"] is None
    other = client(); login(other)
    assert preview(other, did)["branding"] is None
    assert not db.rows("SELECT * FROM wechat_brand_numbers")
    body = "旧落款\n\n仍然是正文，不能删。"
    assert "旧落款" in preview(c, did, body)["html"]
    assert c.post("/api/preview", json={"platform": "wechat", "draft_id": 99999}, headers=H).status_code == 404


def test_start_number_requires_explicit_once_only_configuration(monkeypatch):
    c = client(wechat_branding=configured(0)); login(c)
    did = draft(c)
    assert preview(c, did)["branding"]["needs_start_number"] is True
    assert not db.rows("SELECT * FROM wechat_brand_numbers")
    c.put(f"/api/drafts/{did}", json={"wechat_theme": THEME_IDS[0]}, headers=H).raise_for_status()
    monkeypatch.setattr(publish, "wx_draft", lambda *a, **kw: pytest.fail("Must not send placeholder N"))
    from review_helpers import reviewed
    reviewed(c,did)
    r = c.post(f"/api/drafts/{did}/publish", json={"confirm": True}, headers=H)
    assert r.status_code == 400 and "本篇原创编号" in r.text
    for bad in [0, -1, True, 1.5, "8", 1000001]:
        assert c.put("/api/wechat/branding/start-number", json={"number": bad}, headers=H).status_code == 400
    c.put("/api/wechat/branding/start-number", json={"number": 21}, headers=H).raise_for_status()
    assert preview(c, did)["branding"]["number"] == 21
    assert c.put("/api/wechat/branding/start-number", json={"number": 1}, headers=H).status_code == 409


def test_wechat_payload_uploads_private_card_once_and_matches_preview(monkeypatch):
    config = configured()
    c = client(wechat_branding=config); login(c)
    did = draft(c)
    c.put(f"/api/drafts/{did}", json={"wechat_theme": THEME_IDS[0]}, headers=H).raise_for_status()
    output = preview(c, did)["html"]
    uploads, sent = [], []
    url = "https://mmbiz.qpic.cn/test-card.png"
    monkeypatch.setattr(publish, "wx_upload_inline", lambda *a: uploads.append(a) or url)
    monkeypatch.setattr(publish, "wx_draft", lambda *a, **kw: sent.append(kw["html"]) or {"media_id": "TEST_ONLY"})
    for _ in range(2):
        from review_helpers import reviewed
        reviewed(c,did)
        r = c.post(f"/api/drafts/{did}/publish", json={"confirm": True}, headers=H)
        assert r.status_code == 200, r.text
    assert len(uploads) == 1
    assert sent == [output.replace(f'/api/images/{config["card_image_id"]}', url)] * 2
    assert c.get(f'/api/images/{config["card_image_id"]}').status_code == 200
    assert c.get(f"/api/drafts/{did}").json()["body"] == "原正文。"


def test_missing_card_fails_instead_of_exporting_a_broken_image():
    config = configured()
    images.path_of(images.get(config["card_image_id"])).unlink()
    c = client(wechat_branding=config); login(c)
    r = c.post("/api/preview", json={"platform": "wechat", "body": "原文", "wechat_theme": THEME_IDS[0]}, headers=H)
    assert r.status_code == 400 and "名片图片找不到" in r.text


def test_article_number_can_be_edited_repeatedly_without_rewriting_other_drafts():
    c = client(wechat_branding=configured(0)); login(c)
    did, other = draft(c), draft(c, body="另一篇正文。")
    assert preview(c, did)["branding"]["number"] is None
    before = c.get(f"/api/drafts/{did}").json()
    for number in (6, 12, 4):
        response = c.put(f"/api/drafts/{did}/wechat-number", json={"number": number}, headers=H)
        assert response.status_code == 200 and response.json()["number"] == number
        assert preview(c, did)["branding"]["number"] == number
        assert f"第 {number} 篇 AI 原创" in preview(c, did)["html"]
    after = c.get(f"/api/drafts/{did}").json()
    assert (after["body"], after["updated_at"]) == (before["body"], before["updated_at"])
    # One manually entered article starts the automatic sequence, even without a baseline setting.
    assert preview(c, other)["branding"]["number"] == 13
    # 编号由作者自己决定：允许两篇用同一个数字，只返回提示，不改动另一篇
    shared = c.put(f"/api/drafts/{did}/wechat-number", json={"number": 13}, headers=H)
    assert shared.status_code == 200 and shared.json()["number"] == 13
    assert [item["draft_id"] for item in shared.json()["shared_with"]] == [other]
    assert preview(c, did)["branding"]["number"] == 13
    assert preview(c, other)["branding"]["number"] == 13
    db.reset_for_tests()
    assert preview(c, did)["branding"]["number"] == 13
    assert preview(c, other)["branding"]["number"] == 13


@pytest.mark.parametrize("number", [0, -1, True, 1.5, "8", 1000001])
def test_invalid_manual_number_does_not_change_reservation(number):
    c = client(wechat_branding=configured()); login(c)
    did = draft(c)
    assert preview(c, did)["branding"]["number"] == 8
    assert c.put(f"/api/drafts/{did}/wechat-number", json={"number": number}, headers=H).status_code == 400
    assert preview(c, did)["branding"]["number"] == 8


def test_article_number_update_respects_platform_and_configuration():
    c = client(wechat_branding=configured()); login(c)
    xid = draft(c, platform="x")
    assert c.put(f"/api/drafts/{xid}/wechat-number", json={"number": 8}, headers=H).status_code == 400
    assert c.put('/api/drafts/99999/wechat-number', json={"number": 8}, headers=H).status_code == 404
    other = client(); login(other)
    did = draft(other)
    assert other.put(f"/api/drafts/{did}/wechat-number", json={"number": 8}, headers=H).status_code == 400
    assert not db.rows("SELECT * FROM wechat_brand_numbers")


def test_old_unique_number_table_is_rebuilt_so_numbers_can_repeat():
    """旧库里 number 带 UNIQUE，迁移后同一个编号可以给多篇稿子。"""
    c = client(wechat_branding=configured()); login(c)
    did, other = draft(c), draft(c, body="另一篇正文。")
    conn = db.get()
    conn.execute("DROP TABLE wechat_brand_numbers")
    conn.execute("""CREATE TABLE wechat_brand_numbers (
        draft_id INTEGER PRIMARY KEY REFERENCES drafts(id) ON DELETE CASCADE,
        number INTEGER NOT NULL UNIQUE)""")
    conn.commit()
    db.reset_for_tests()
    conn = db.get()
    assert [row["name"] for row in conn.execute("PRAGMA index_list(wechat_brand_numbers)") if row["unique"]] == []
    with db.tx() as tx:
        tx.execute("INSERT INTO wechat_brand_numbers(draft_id,number) VALUES(?,?)", (did, 9))
        tx.execute("INSERT INTO wechat_brand_numbers(draft_id,number) VALUES(?,?)", (other, 9))
    assert [row["number"] for row in db.rows("SELECT number FROM wechat_brand_numbers ORDER BY draft_id")] == [9, 9]
