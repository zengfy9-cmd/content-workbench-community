import json

from content_workbench import db, jobs, recording_memory as memory
from content_workbench.config import Settings


def material(body, source="feishu_minutes"):
    with db.tx() as c:
        return c.execute(
            "INSERT INTO materials(source,source_ref,title,body,created_at) VALUES (?,?,?,?,?)",
            (source, str(db.now()), "历史录音", body, db.now())
        ).lastrowid


def test_related_recording_preserves_original_passage_and_excludes_references():
    text = "无关的开场。" * 350 + "\n说话人 2 00:30:00\n我做课程样片，先演示再解释。"
    mid = material(text)
    material("课程样片 讲课 演示", "paste")
    hits = memory.search("课程教学")
    assert hits and hits[0]["material_id"] == mid
    hit = hits[0]
    assert hit["excerpt"] == text[hit["start"]:hit["end"]]
    assert "说话人 2" in hit["excerpt"]
    assert all(x["material_id"] == mid for x in hits)
    assert memory.search("火星采矿机器人") == []


def test_recording_synonyms_and_no_network():
    material("我用语音把昨天发生的事情说下来，留成逐字稿。")
    assert memory.search("录音豆")


def test_draft_receives_cross_material_evidence_and_saves_provenance(monkeypatch):
    mid = material("说话人 2 00:02:01\n我们讨论了课程样片应该先演示还是先解释。")
    with db.tx() as c:
        iid = c.execute("INSERT INTO ideas(title,angle,quotes,created_at) VALUES (?,?,?,?)",
                        ("课程样片制作", "教学演示", "[]", db.now())).lastrowid
        did = c.execute("INSERT INTO drafts(idea_id,platform,created_at,updated_at) VALUES (?,?,?,?)",
                        (iid, "x", db.now(), db.now())).lastrowid
    captured = []
    monkeypatch.setattr(jobs.prompts, "build_draft", lambda *a: "起草")
    monkeypatch.setattr(jobs.prompts, "case_search_on", lambda *a: False)
    monkeypatch.setattr(jobs.prompts, "voice_flags", lambda *a: [])
    def run(prompt, **kw):
        captured.append(prompt)
        return json.dumps({"title": "课程样片", "body": "现场讨论了两种教学顺序。", "comments": []}, ensure_ascii=False)
    monkeypatch.setattr(jobs.llm, "run", run)
    jobs._draft(Settings(), {"draft_id": did})
    assert "先演示还是先解释" in captured[0]
    assert "无法识别说话人" in captured[0]
    saved = db.row("SELECT body FROM comments WHERE draft_id=?", (did,))["body"]
    assert f"素材 #{mid}" in saved and "00:02:01" in saved
    assert "候选，不代表全部采用" in saved
    assert "历史录音" not in db.row("SELECT body FROM drafts WHERE id=?", (did,))["body"]
