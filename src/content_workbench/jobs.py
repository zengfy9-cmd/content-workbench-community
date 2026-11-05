"""Background jobs: imports, transcription, topic extraction and drafting.
A few workers run model calls in parallel; Whisper transcription stays on one.
A sync thread pulls the author's new Feishu Minutes every two hours."""

from __future__ import annotations

import datetime
import json
import re
import threading
import time
import traceback
from pathlib import Path

from . import comments, db, feishu, learning, llm, prompts, sources
from .config import Settings, data_dir

_wake = threading.Event()
_started = False


def wake() -> None:
    _wake.set()


def enqueue(kind: str, payload: dict) -> int:
    with db.tx() as conn:
        cur = conn.execute("INSERT INTO jobs(kind, payload, created_at, updated_at) VALUES (?,?,?,?)",
                           (kind, db.dumps(payload), db.now(), db.now()))
        job_id = cur.lastrowid
    _wake.set()
    return job_id


def _set(job_id: int, state: str, error: str | None = None) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE jobs SET state=?, error=?, updated_at=? WHERE id=? AND state<>'cancelled'", (state, error, db.now(), job_id))


def start(settings: Settings) -> None:
    global _started
    if _started:
        return
    _started = True
    if settings.llm_backend == 'codex-cli':
        # Do not silently spend subscription allowance by replaying interrupted inference.
        for job in db.rows("SELECT * FROM jobs WHERE state='running'"):
            if job['kind'] in ('draft','extract','cases','xhs','revise','benchmark_analyze','video_plan','media_draft','intake_item'):
                error=RuntimeError('服务重启中断了 Codex 任务；请先核对已有结果，没有自动重新生成')
                _set(job['id'],'failed',str(error))
                _mark_failure(job['kind'],json.loads(job['payload']),error)
    with db.tx() as conn:  # jobs interrupted by a restart go back to the queue
        conn.execute("UPDATE jobs SET state='queued' WHERE state='running'")
        conn.execute("UPDATE drafts SET feishu_state='failed', feishu_error='上次同步被重启打断' WHERE feishu_state='syncing'")
        conn.execute("UPDATE drafts SET status='draft' WHERE status='revising' AND id NOT IN "
                     "(SELECT json_extract(payload, '$.draft_id') FROM jobs WHERE kind='revise' AND state='queued')")
    # Model calls run remotely, so a few in parallel is fine; transcription stays
    # on one worker so two Whisper runs never compete for the GPU.
    for n in range(WORKERS):
        threading.Thread(target=_loop, args=(settings, n == 0), daemon=True, name=f"cw-jobs-{n}").start()
    threading.Thread(target=_auto_sync, args=(settings,), daemon=True, name="cw-sync").start()
    threading.Thread(target=_archive_loop, daemon=True, name="cw-archive").start()


WORKERS = 3


def _claim(allow_heavy: bool) -> dict | None:
    with db.tx() as conn:
        # Whisper and video rendering use this machine's GPU/CPU: one at a time, on one worker
        kinds = "" if allow_heavy else "AND kind NOT IN ('transcribe', 'video_render', 'media_import', 'intake_media', 'video_research')"
        job = conn.execute(f"SELECT * FROM jobs WHERE state='queued' {kinds} AND (depends_on IS NULL OR NOT EXISTS(SELECT 1 FROM jobs parent WHERE parent.id=jobs.depends_on AND parent.state IN ('queued','running'))) ORDER BY id LIMIT 1").fetchone()
        if not job:
            return None
        cur = conn.execute("UPDATE jobs SET state='running', updated_at=? WHERE id=? AND state='queued'", (db.now(), job["id"]))
        return dict(job) if cur.rowcount == 1 else None


def _loop(settings: Settings, allow_heavy: bool = True) -> None:
    while True:
        job = _claim(allow_heavy)
        if not job:
            _wake.wait(timeout=5)
            _wake.clear()
            continue
        try:
            run_job(settings, job["kind"], json.loads(job["payload"]))
            if db.row("SELECT state FROM jobs WHERE id=?", (job["id"],))["state"] != "cancelled":
                _set(job["id"], "done")
        except Exception as exc:  # recorded on the job and on the target row
            if db.row("SELECT state FROM jobs WHERE id=?", (job["id"],))["state"] == "cancelled":
                continue
            _set(job["id"], "failed", str(exc)[:500])
            _mark_failure(job["kind"], json.loads(job["payload"]), exc)
            traceback.print_exc()


def _mark_failure(kind: str, payload: dict, exc: Exception) -> None:
    if kind in ('intake_item','intake_media'):
        from . import intake_batches
        intake_batches.failed(payload,exc)
        return
    if kind == "media_import":
        return
    if kind == "benchmark_import":
        from . import benchmark_imports
        benchmark_imports.failed(payload, exc)
        return
    if kind == "benchmark_analyze":
        from . import benchmarks
        benchmarks.failed(payload, exc)
        return
    with db.tx() as conn:
        if kind == "transcribe":
            conn.execute("UPDATE materials SET status='failed', error=? WHERE id=?", (str(exc)[:500], payload["material_id"]))
        elif kind == "extract":
            conn.execute("UPDATE materials SET error=? WHERE id=?", (f"提炼失败：{exc}"[:500], payload["material_id"]))
        elif kind in ("draft", "media_draft"):
            conn.execute("UPDATE drafts SET status='failed', error=? WHERE id=?", (str(exc)[:500], payload["draft_id"]))
        elif kind == "cases":
            conn.execute("UPDATE case_searches SET state='failed', error=? WHERE id=?", (str(exc)[:500], payload["search_id"]))
        elif kind == "xhs":
            from . import xhs
            d = db.row("SELECT * FROM drafts WHERE id=?", (payload["draft_id"],))
            state = xhs.load(d)
            state.update(generating=False, error=f"生成小红书版失败：{exc}"[:300])
            xhs.save(payload["draft_id"], state)
        elif kind == "revise":
            conn.execute("UPDATE drafts SET status='draft', error=? WHERE id=?", (f"按批注修改失败：{exc}"[:500], payload["draft_id"]))
        elif kind in ("video_plan", "video_render"):
            from . import video
            d = db.row("SELECT video FROM drafts WHERE id=?", (payload["draft_id"],))
            state = video.load(d or {})
            what = "写视频脚本" if kind == "video_plan" else "出片"
            state.update(status="", stage="", error=f"{what}失败：{exc}"[:400])
            conn.execute("UPDATE drafts SET video=? WHERE id=?", (json.dumps(state, ensure_ascii=False), payload["draft_id"]))
        elif kind == "import_feishu":
            failed = library_state().get("failed", {})
            failed[payload["token"]] = str(exc)[:200]
            _save_library_state(failed=failed)


def run_job(settings: Settings, kind: str, payload: dict) -> None:
    if kind=='video_research':
        from . import video_research
        return video_research.run(payload)
    if kind in ('intake_item','intake_media'):
        from . import intake_batches
        return intake_batches.run(payload)
    if kind in ('media_import','media_draft'):
        from . import media_imports
        return media_imports.run(payload) if kind=='media_import' else media_imports.draft_job(payload)
    if kind == "benchmark_import":
        from . import benchmark_imports
        benchmark_imports.run(payload)
    elif kind == "benchmark_analyze":
        from . import benchmarks
        benchmarks.analyze(settings, payload)
    elif kind == "transcribe":
        _transcribe(payload)
    elif kind == "extract":
        _extract(settings, payload)
    elif kind == "draft":
        _draft(settings, payload)
    elif kind == "import_feishu":
        _import_feishu(payload)
    elif kind == "import_minute":
        _import_minute(payload)
    elif kind == "refresh_library":
        refresh_library()
    elif kind == "feishu_sync":
        feishu.sync(payload["draft_id"], force=bool(payload.get("force")))
    elif kind == "cases":
        _cases(payload)
    elif kind == "revise":
        comments.revise(settings, payload["draft_id"])
    elif kind == "learn":
        learning.learn()
    elif kind == "video_plan":
        from . import video
        video.plan(settings, payload["draft_id"])
    elif kind == "video_render":
        from . import video
        video.render(settings, payload["draft_id"])
    elif kind == "xhs":
        from . import xhs
        try:
            xhs.generate(settings, payload["draft_id"])
        finally:
            d = db.row("SELECT * FROM drafts WHERE id=?", (payload["draft_id"],))
            state = xhs.load(d)
            state.pop("generating", None)
            xhs.save(payload["draft_id"], state)
    else:
        raise ValueError(f"unknown job {kind}")


def _transcribe(payload: dict) -> None:
    path = Path(payload["path"])
    text = sources.transcribe(path)
    with db.tx() as conn:
        conn.execute("UPDATE materials SET body=?, status='ready', error=NULL WHERE id=?", (text, payload["material_id"]))
    if payload.get("keep_audio") is False:
        path.unlink(missing_ok=True)


CHUNK = 36000
EXISTING_IDEAS = 400  # most recent idea titles shown to the extractor


def _chunks(text: str) -> list[str]:
    """Split long material on paragraph breaks so a 4-hour transcript is read in
    full instead of being cut at the prompt cap."""
    if len(text) <= CHUNK:
        return [text]
    parts, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) > CHUNK and cur:
            parts.append(cur)
            cur = ""
        cur += para + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


def _extract(settings: Settings, payload: dict) -> None:
    material = db.row("SELECT * FROM materials WHERE id=?", (payload["material_id"],))
    if not material or not material["body"].strip():
        raise ValueError("素材是空的")
    body = material["body"]
    ideas = []
    pieces = _chunks(body)
    # the same talk often arrives as a transcript, a doc and a rewrite: show the
    # model what is already there so one point doesn't become three ideas
    existing = [r["title"] for r in db.rows("SELECT title FROM ideas ORDER BY id DESC LIMIT ?", (EXISTING_IDEAS,))]
    for n, piece in enumerate(pieces, 1):
        part = dict(material, body=piece)
        if len(pieces) > 1:
            part["title"] = f"{material['title']}（第 {n}/{len(pieces)} 段）"
        found = [str(i.get("title", "")) for i in ideas if isinstance(i, dict)]
        data = llm.extract_json(llm.run(prompts.build_extract(settings, part, found + existing), task="analyze", json_output=True))
        ideas += data.get("ideas", []) if isinstance(data, dict) else data
    with db.tx() as conn:
        for idea in ideas[:8 * len(pieces)]:
            # keep only quotes that really occur in the material
            quotes = [q for q in (idea.get("quotes") or []) if isinstance(q, str) and q.strip() and q.strip()[:20] in body]
            conn.execute("INSERT INTO ideas(media_id, material_id, title, angle, platforms, quotes, notes, created_at) VALUES (?,?,?,?,?,?,?,?)",
                         (material.get("media_id"), material["id"], str(idea.get("title", ""))[:200], str(idea.get("angle", ""))[:2000],
                          db.dumps(idea.get("platforms") or []), db.dumps(quotes), str(idea.get("notes", ""))[:2000], db.now()))
        conn.execute("UPDATE materials SET error=NULL WHERE id=?", (material["id"],))


def _draft(settings: Settings, payload: dict) -> None:
    draft = db.row("SELECT * FROM drafts WHERE id=?", (payload["draft_id"],))
    idea = db.row("SELECT * FROM ideas WHERE id=?", (draft["idea_id"],))
    material = db.row("SELECT * FROM materials WHERE id=?", (idea["material_id"],)) if idea and idea["material_id"] else None
    quotes = db.loads(idea["quotes"], [])
    tools = llm.SCYS_TOOLS if prompts.case_search_on(settings) else None
    context = db.loads(draft.get('writing_context'), None)
    if context:
        from .idea_cards import context_prompt
        prompt = context_prompt(context, draft['platform'])
        tools = None  # Explicitly selected writing inputs; no automatic external case lookup.
    else:
        prompt = prompts.build_draft(settings, draft['platform'], idea, material, quotes)
    from . import recording_memory
    memory_query = str((context or {}).get("title") or idea.get("title") or "") + " " + str(idea.get("angle") or "")
    memories = recording_memory.search(memory_query)
    if memories:
        prompt += recording_memory.prompt(memories)
    reply = llm.run(prompt,
                    timeout=1200, tools=tools, max_turns=12 if tools else 2, task="write", json_output=True)
    result = llm.extract_json(reply)
    mode = "X" if draft["platform"] == "x" else "公众号"
    flags = prompts.voice_flags(settings, result.get("body", ""), mode)
    if flags:
        revised = llm.extract_json(llm.run(prompts.build_revise(settings, flags, json.dumps(result, ensure_ascii=False)), task="write", json_output=True))
        if revised.get("body"):
            result = revised
        flags = prompts.voice_flags(settings, result.get("body", ""), mode)
    if draft["platform"] != "x":
        from .textutil import normalize_emphasis
        result["body"] = normalize_emphasis(str(result.get("body", "")))
    notes = str(result.get("notes", ""))
    if flags:
        notes += "\n\n文风检查还提示：" + "；".join(flags) + "（只是提醒，回去重读这几处就行）"
    body = comments.PLACEHOLDER.sub("", str(result.get("body", "")))
    with db.tx() as conn:
        conn.execute("UPDATE drafts SET title=?, body=?, notes=?, status='draft', error=NULL, updated_at=? WHERE id=?",
                     (str(result.get("title", ""))[:200], body, notes.strip(), db.now(), draft["id"]))
        conn.execute("INSERT INTO draft_versions(draft_id, title, body, created_at, origin) VALUES (?,?,?,?, 'generated')",
                     (draft["id"], result.get("title", ""), result.get("body", ""), db.now()))
        conn.execute("UPDATE ideas SET status='drafted' WHERE id=?", (idea["id"],))
    if memories:
        # Persist retrieved evidence independently of the model's willingness to cite it.
        comments.add(draft["id"], "ai", "起草参考的历史录音（候选，不代表全部采用）：\n" + "\n\n".join(
            f"素材 #{m['material_id']}：{m['title']}\n{m['source_url']}\n"
            f"工作台：/#/materials/{m['material_id']}\n原文字位 {m['start']}–{m['end']}\n{m['excerpt']}"
            for m in memories))
    for c in result.get("comments") or []:
        if isinstance(c, dict) and str(c.get("text", "")).strip():
            comments.add(draft["id"], "ai", str(c["text"]), quote=str(c.get("quote") or "").strip()[:80] or None)


def _cases(payload: dict) -> None:
    search = db.row("SELECT * FROM case_searches WHERE id=?", (payload["search_id"],))
    draft = db.row("SELECT title, body FROM drafts WHERE id=?", (search["draft_id"],)) or {}
    reply = llm.run(prompts.build_cases(search["query"], draft.get("title", ""), draft.get("body", "")),
                    timeout=900, tools=llm.SCYS_TOOLS, max_turns=10, task="analyze", json_output=True)  # only reachable when case search is on
    found = llm.extract_json(reply)
    cases = found.get("cases", []) if isinstance(found, dict) else found
    with db.tx() as conn:
        conn.execute("UPDATE case_searches SET state='done', results=?, error=NULL WHERE id=?",
                     (db.dumps(cases[:8]), search["id"]))


def uploads_dir() -> Path:
    path = data_dir() / "uploads"
    path.mkdir(exist_ok=True)
    return path


# ------------------------------------------------------------------ Feishu library

def library_path() -> Path:
    return data_dir() / "feishu_library.json"


def library_state() -> dict:
    path = library_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_library_state(**updates) -> None:
    from .config import _write_private
    state = library_state()
    state.update(updates)
    _write_private(library_path(), json.dumps(state, ensure_ascii=False))


def refresh_library() -> None:
    _save_library_state(refreshing=True)
    try:
        items = sources.list_my_docs()
        _save_library_state(items=items, updated_at=time.time(), refreshing=False)
    except Exception:
        _save_library_state(refreshing=False)
        raise


def _import_feishu(payload: dict) -> None:
    token = payload["token"]
    if db.row("SELECT id FROM materials WHERE source IN ('feishu_doc','feishu_table') AND source_ref=?", (token,)):
        return
    kind = payload.get("type") or "docx"
    if kind in ("sheet", "bitable"):
        title, text = payload.get("title") or "飞书表格", sources.fetch_table(payload["url"], kind)
    else:
        title, text = sources.fetch_doc(payload["url"])
    if not text.strip():
        raise sources.SourceError("文档是空的")
    with db.tx() as conn:
        cur = conn.execute("INSERT INTO materials(source, source_ref, title, body, created_at) VALUES (?, ?, ?, ?, ?)",
                           ("feishu_table" if kind in ("sheet", "bitable") else "feishu_doc", token,
                            (payload.get("title") or title)[:200], text, payload.get("created") or db.now()))
        mid = cur.lastrowid
    enqueue("extract", {"material_id": mid})


def _import_minute(payload: dict) -> None:
    token = payload["token"]
    if db.row("SELECT id FROM materials WHERE source='feishu_minutes' AND source_ref=?", (token,)):
        return
    text = sources.fetch_minute_transcript(token)
    if not text.strip():
        raise sources.SourceError("这条妙记没有逐字稿")
    with db.tx() as conn:
        cur = conn.execute("INSERT INTO materials(source, source_ref, title, body, created_at) VALUES ('feishu_minutes', ?, ?, ?, ?)",
                           (token, (payload.get("title") or "飞书妙记")[:200], text, payload.get("created") or db.now()))
        mid = cur.lastrowid
    if not payload.get("capture_only"):
        enqueue("extract", {"material_id": mid})


# ------------------------------------------------------------------ auto sync

SYNC_EVERY = 2 * 3600
def sync_minutes(settings: Settings) -> int:
    """Scan all owned recordings, including short and previously missed ones."""
    have = {r["source_ref"] for r in db.rows("SELECT source_ref FROM materials WHERE source='feishu_minutes'")}
    # A separately imported verbatim document can already point to its paired
    # summary. Only skip if both exist and the summary identifies the recording.
    docs = {r["source_ref"]: r["body"] for r in db.rows("SELECT source_ref,body FROM materials WHERE source='feishu_doc'")}
    for body in docs.values():
        match = re.search(r"智能纪要：\[[^\n]*?\]\((https://[^)]+)\)", body[:2000])
        if match and match[1] in docs:
            have.update(re.findall(r"/minutes/([a-z0-9]{10,64})", docs[match[1]]))
    pending = {json.loads(r["payload"]).get("token") for r in db.rows("SELECT payload FROM jobs WHERE kind='import_minute' AND state IN ('queued','running')")}
    count = 0
    for it in sources.owned_minutes():
        token = it["token"]
        if token in have or token in pending:
            continue
        pending.add(token)
        enqueue("import_minute", {"token": token, "title": it["title"], "capture_only": True})
        count += 1
    return count


ARCHIVE_EVERY = 60


def enqueue_archive(draft_id: int, force: bool = False) -> int | None:
    busy = [json.loads(r["payload"]).get("draft_id") for r in
            db.rows("SELECT payload FROM jobs WHERE kind='feishu_sync' AND state IN ('queued','running')")]
    if draft_id in busy and not force:
        return None
    return enqueue("feishu_sync", {"draft_id": draft_id, "force": force})


def _archive_loop() -> None:
    """Every draft gets archived in its own Feishu doc a little after edits settle."""
    time.sleep(30)
    while True:
        try:
            for draft_id in (feishu.pending() if Settings.load().feishu_auto_archive else []):
                enqueue_archive(draft_id)
        except Exception:
            traceback.print_exc()
        try:  # the author's edits on AI text, once he has left a draft alone for a while
            for d in learning.idle_drafts():
                learning.record_round(d["id"], d["body"], "idle")
            busy = db.row("SELECT 1 x FROM jobs WHERE kind='learn' AND state IN ('queued','running')")
            if not busy and len(learning.pending_rounds()) >= learning.LEARN_EVERY:
                enqueue("learn", {})
        except Exception:
            traceback.print_exc()
        time.sleep(ARCHIVE_EVERY)


def _auto_sync(settings: Settings) -> None:
    time.sleep(60)
    while True:
        try:
            if Settings.load().feishu_auto_sync:
                sync_minutes(settings)
        except Exception:
            traceback.print_exc()
        time.sleep(SYNC_EVERY)
