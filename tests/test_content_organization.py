"""Content management on isolated databases; no real content or remote platforms."""

import sqlite3

import pytest

from content_workbench import db
from test_app import ORIGIN, client, login
from test_longform import png, upload

H = {"Origin": ORIGIN}


def authed():
    c = client()
    login(c)
    return c


def group(c, name="示例项目"):
    r = c.post("/api/content-groups", json={"name": name}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def idea(c, title="预约怎么核对"):
    return c.post("/api/ideas", json={"title": title}, headers=H).json()["id"]


def draft(c, title="已有正文"):
    return c.post("/api/drafts", json={"platform": "wechat", "title": title, "body": "不能丢的正文"}, headers=H).json()["id"]


def test_existing_database_migration_preserves_content_and_is_repeatable(private_dir):
    # Real pre-feature tables without the new column, upgraded by create_app/db.get.
    conn = sqlite3.connect(db.db_path())
    old_schema = db.SCHEMA.replace(db.SCHEMA[db.SCHEMA.index("CREATE TABLE IF NOT EXISTS content_groups"):
                                          db.SCHEMA.index("CREATE TABLE IF NOT EXISTS ideas")], "")
    conn.executescript(old_schema)
    conn.execute("INSERT INTO ideas(title, created_at) VALUES ('原有选题', 1)")
    conn.execute("INSERT INTO drafts(platform, body, created_at, updated_at) VALUES ('wechat', '原有正文', 1, 1)")
    conn.commit()
    conn.close()
    c = authed()
    assert c.get("/api/ideas").json()[0]["group_id"] is None
    assert c.get("/api/drafts/1").json()["body"] == "原有正文"
    db.migrate(db.get())
    assert c.get("/api/content-groups").json() == []


def test_grouping_persists_and_never_overwrites_content():
    c = authed()
    iid, did, other = idea(c), draft(c), draft(c, "另一篇")
    gid = group(c)
    before = c.get(f"/api/drafts/{did}").json()
    for kind, ident in (("ideas", iid), ("drafts", did)):
        r = c.patch(f"/api/{kind}/{ident}", json={"group_id": gid}, headers=H)
        assert r.status_code == 200
        item = next(x for x in c.get(f"/api/{kind}").json() if x["id"] == ident)
        assert item["group_id"] == gid and item["group_name"] == "示例项目"
    after = c.get(f"/api/drafts/{did}").json()
    assert (after["title"], after["body"], after["updated_at"]) == (before["title"], before["body"], before["updated_at"])
    assert c.get(f"/api/drafts/{other}").json()["group_id"] is None
    db.reset_for_tests()
    c = authed()
    assert c.get("/api/content-groups").json() == [{"id": gid, "name": "示例项目"}]
    assert c.get(f"/api/drafts/{did}").json()["group_id"] == gid
    assert c.patch(f"/api/drafts/{did}", json={"group_id": None}, headers=H).status_code == 200
    assert c.get(f"/api/drafts/{did}").json()["group_id"] is None


def test_group_validation_and_duplicate_creation():
    c = authed()
    gid = group(c, "  WorkBuddy  ")
    assert group(c, "workbuddy") == gid
    for invalid in ("", "  ", "x" * 41, None, 123):
        assert c.post("/api/content-groups", json={"name": invalid}, headers=H).status_code == 400
    for kind, ident in (("ideas", idea(c)), ("drafts", draft(c))):
        for bad in (99999, "1", True, {}, []):
            assert c.patch(f"/api/{kind}/{ident}", json={"group_id": bad}, headers=H).status_code == 400
        assert c.patch(f"/api/{kind}/99999", json={"group_id": gid}, headers=H).status_code == 404


def test_draft_inherits_group_and_idea_delete_preserves_written_draft():
    c = authed()
    mid = c.post("/api/materials", json={"body": "原始素材", "extract": False}, headers=H).json()["id"]
    iid = c.post("/api/ideas", json={"title": "引用的选题", "material_id": mid}, headers=H).json()["id"]
    gid = group(c)
    c.patch(f"/api/ideas/{iid}", json={"group_id": gid}, headers=H)
    did = c.post(f"/api/ideas/{iid}/draft", json={"platform": "wechat"}, headers=H).json()["id"]
    assert c.get(f"/api/drafts/{did}").json()["group_id"] == gid
    assert c.delete(f"/api/ideas/{iid}", headers=H).status_code == 409
    with db.tx() as conn:
        conn.execute("UPDATE drafts SET status='draft', body='已写好的稿子' WHERE id=?", (did,))
        conn.execute("UPDATE jobs SET state='done'")
    other = idea(c, "保留的选题")
    assert c.delete(f"/api/ideas/{iid}", headers=H).status_code == 200
    assert [x["id"] for x in c.get("/api/ideas?status=all").json()] == [other]
    d = c.get(f"/api/drafts/{did}").json()
    assert d["body"] == "已写好的稿子" and d["idea_id"] is None and d["group_id"] == gid
    assert c.get(f"/api/materials/{mid}").json()["body"] == "原始素材"
    assert c.delete(f"/api/ideas/{iid}", headers=H).status_code == 404


def test_delete_single_draft_removes_only_its_dependents():
    c = authed()
    did, other = draft(c), draft(c, "保留")
    image_id = upload(c, did, png())[0]
    c.put(f"/api/drafts/{did}", json={"body": "一个历史版本"}, headers=H)
    assert c.get(f"/api/drafts/{did}/versions").json()
    assert c.delete(f"/api/drafts/{did}", headers=H).status_code == 200
    assert c.get(f"/api/drafts/{did}").status_code == 404
    assert c.get(f"/api/images/{image_id}").status_code == 404
    assert db.rows("SELECT * FROM draft_versions WHERE draft_id=?", (did,)) == []
    assert c.get(f"/api/drafts/{other}").json()["body"] == "不能丢的正文"


@pytest.mark.parametrize("status", ["generating", "revising"])
def test_cannot_delete_active_draft(status):
    c = authed()
    did = draft(c)
    with db.tx() as conn:
        conn.execute("UPDATE drafts SET status=? WHERE id=?", (status, did))
    assert c.delete(f"/api/drafts/{did}", headers=H).status_code == 409
    assert c.get(f"/api/drafts/{did}").json()["body"] == "不能丢的正文"


def test_group_and_delete_require_login_and_origin():
    c = client()
    assert c.get("/api/content-groups").status_code == 401
    assert c.delete("/api/ideas/1", headers=H).status_code == 401
    login(c)
    did = draft(c)
    assert c.post("/api/content-groups", json={"name": "x"}).status_code == 403
    assert c.patch(f"/api/drafts/{did}", json={"group_id": None}).status_code == 403
    assert c.delete(f"/api/drafts/{did}").status_code == 403
