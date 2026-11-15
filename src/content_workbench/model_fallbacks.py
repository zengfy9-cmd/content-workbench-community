"""Failed text processing may move to DeepSeek only after item-specific consent.

No global model switch, automatic approval, or image/audio/download fallback.
Approval and enqueue share one transaction; source snapshots guard stale requests.
"""
from __future__ import annotations

import hashlib
from fastapi import APIRouter, Body, HTTPException

from . import db, deepseek_api, llm, research_protocol

router = APIRouter()


def stop_interrupted():
    """A restart must not silently repeat a previously authorized paid attempt."""
    from . import jobs, media_imports
    for job in db.rows("SELECT * FROM jobs WHERE state='running' AND (id IN "
                       "(SELECT retry_job_id FROM model_fallback_requests WHERE state='approved') "
                       "OR id IN (SELECT job_id FROM intake_items WHERE json_extract(route,'$.fallback.authorization_id') IS NOT NULL) "
                       "OR json_extract(payload,'$.route.fallback.authorization_id') IS NOT NULL "
                       "OR json_extract(payload,'$.model_route.fallback.authorization_id') IS NOT NULL)"):
        payload = db.loads(job['payload'], {})
        error = RuntimeError('服务重启中断了已授权的 DeepSeek 任务；请先核对已有结果，再手动重试。没有自动再次调用 API')
        jobs._set(job['id'], 'failed', str(error))
        jobs._mark_failure(job['kind'], payload, error)
        if job['kind'] == 'media_import':
            media_imports.write(payload['media_id'], state='failed', error=str(error))


def _hash(text):
    return hashlib.sha256((text or '').encode()).hexdigest()


def _snapshot(spec):
    values = {}
    if spec.get('item_id'):
        item = db.row('SELECT * FROM intake_items WHERE id=?', (spec['item_id'],))
        if not item:
            return None
        values['fingerprint'] = item['fingerprint']
    if spec.get('benchmark_id'):
        source = db.row('SELECT body_hash FROM benchmarks WHERE id=?', (spec['benchmark_id'],))
        if not source:
            return None
        values['body_hash'] = source['body_hash']
    if spec.get('media_id'):
        source = db.row('SELECT raw FROM media_imports WHERE id=?', (spec['media_id'],))
        if not source:
            return None
        values['raw_hash'] = _hash(source['raw'])
    return values


def record(kind, payload, exc):
    if not isinstance(exc, llm.ModelBackendUnavailable) or exc.backend == 'deepseek-api':
        return
    spec = {'kind': kind}
    if kind in ('intake_item', 'intake_media'):
        item = db.row('SELECT * FROM intake_items WHERE id=?', (payload['item_id'],))
        if not item or item['depth'] == 'archive':
            return
        jid = payload.get('job_id', item['job_id'])
        spec.update(item_id=item['id'], batch_id=item['batch_id'], media_id=item['media_id'],
                    benchmark_id=item['benchmark_id'], title=item['title'] or '导入素材',
                    depth=item['depth'], route=db.loads(item['route'], {}))
    elif kind == 'media_import':
        source = db.row('SELECT * FROM media_imports WHERE id=?', (payload['media_id'],))
        if not source or source['stage'] != 3 or source['distiller'] != 'workbench' or not source['raw'].strip():
            return
        jid = source['job_id']
        spec.update(media_id=source['id'], title=source['title'], depth='deep', route=payload.get('model_route') or {})
    elif kind == 'benchmark_analyze':
        jid = payload['job_id']
        source = db.row('SELECT * FROM benchmarks WHERE id=?', (payload['benchmark_id'],))
        if not source:
            return
        item = db.row('SELECT * FROM intake_items WHERE analysis_job_id=?', (jid,))
        spec.update(benchmark_id=source['id'], title=source['title'], depth=payload.get('depth', 'deep'), route=payload.get('route') or {})
        if item:
            spec.update(item_id=item['id'], batch_id=item['batch_id'], media_id=item['media_id'])
    else:
        return
    job = db.row('SELECT state FROM jobs WHERE id=?', (jid,))
    if not job or job['state'] != 'failed':
        return
    spec['snapshot'] = _snapshot(spec)
    if spec['snapshot'] is None:
        return
    spec['choice'] = 'deepseek-flash' if spec['depth'] == 'basic' else 'deepseek-pro'
    with db.tx() as c:
        c.execute('INSERT OR IGNORE INTO model_fallback_requests(job_id,spec,reason,source_backend,source_model,created_at) VALUES(?,?,?,?,?,?)',
                  (jid, db.dumps(spec), str(exc)[:400], exc.backend, exc.model, db.now()))


def _valid(request, spec):
    job = db.row('SELECT state FROM jobs WHERE id=?', (request['job_id'],))
    if not job or job['state'] != 'failed' or _snapshot(spec) != spec['snapshot']:
        return False
    if spec.get('item_id'):
        from .intake_batches import effective
        item = db.row('SELECT * FROM intake_items WHERE id=?', (spec['item_id'],))
        return bool(item and not item['duplicate_of'] and effective(item)['state'] == 'failed'
                    and (item['analysis_job_id'] if spec['kind'] == 'benchmark_analyze' else item['job_id']) == request['job_id'])
    if spec['kind'] == 'media_import':
        source = db.row('SELECT state,job_id FROM media_imports WHERE id=?', (spec['media_id'],))
        return bool(source and source['state'] == 'failed' and source['job_id'] == request['job_id'])
    source = db.row('SELECT analysis_state FROM benchmarks WHERE id=?', (spec['benchmark_id'],))
    last = db.row("SELECT id FROM jobs WHERE kind='benchmark_analyze' AND json_extract(payload,'$.benchmark_id')=? ORDER BY id DESC LIMIT 1", (spec['benchmark_id'],))
    return bool(source and source['analysis_state'] == 'failed' and last and last['id'] == request['job_id'])


@router.get('/api/model-fallbacks')
def pending():
    result = []
    for row in db.rows("SELECT * FROM model_fallback_requests WHERE state='pending' ORDER BY id"):
        spec = db.loads(row['spec'], {})
        if not _valid(row, spec):
            with db.tx() as c:
                c.execute("UPDATE model_fallback_requests SET state='obsolete',decided_at=? WHERE id=? AND state='pending'", (db.now(), row['id']))
            continue
        result.append({'id': row['id'], 'title': spec['title'], 'reason': row['reason'],
                       'source_backend': row['source_backend'], 'source_model': row['source_model'],
                       'target_model': deepseek_api.model_for('research-basic' if spec['depth'] == 'basic' else 'research-deep'),
                       'task': '素材整理与研究' if spec.get('item_id') else '素材整理' if spec['kind'] == 'media_import' else '原文研究',
                       'href': f"#/imports/{spec['media_id']}" if spec.get('media_id') else f"#/benchmarks/{spec['benchmark_id']}" if spec.get('benchmark_id') else '#/imports'})
    return {'requests': result, 'deepseek_ready': deepseek_api.status()['ok']}


@router.post('/api/model-fallbacks/{rid}/decline')
def decline(rid: int):
    with db.tx() as c:
        request = c.execute('SELECT state FROM model_fallback_requests WHERE id=?', (rid,)).fetchone()
        if not request:
            raise HTTPException(404, '授权请求不存在')
        if request['state'] not in ('pending','declined'):
            raise HTTPException(409, '这项请求已授权或已过期；如需停止，请到原任务操作')
        c.execute("UPDATE model_fallback_requests SET state='declined',decided_at=? WHERE id=? AND state='pending'", (db.now(), rid))
    return {'ok': True}


def _set_item_route(c, item_id, route, stamp):
    item = dict(c.execute('SELECT * FROM intake_items WHERE id=?', (item_id,)).fetchone())
    raw = item['input_value']
    if item['input_kind'] == 'file':
        source = c.execute('SELECT file_hash FROM media_imports WHERE id=?', (int(raw),)).fetchone()
        if not source:
            raise HTTPException(409, '原上传记录不存在，请回原任务核对')
        key = 'file:' + source['file_hash']
    else:
        key = 'url:' + raw if item['input_kind'] == 'url' else 'text:' + _hash(raw)
    fingerprint = _hash(db.dumps({'source': key, 'depth': item['depth'], 'destination': item['destination'], 'route': route, 'asr': item['asr_model']}))
    c.execute('UPDATE intake_items SET route=?,fingerprint=?,updated_at=? WHERE id=?', (db.dumps(route),fingerprint,stamp,item_id))
    return item


@router.post('/api/model-fallbacks/{rid}/approve')
def approve(rid: int, body: dict = Body(...)):
    if body.get('confirm') is not True:
        raise HTTPException(400, '需要明确同意本项使用 DeepSeek API')
    if not deepseek_api.status()['ok']:
        raise HTTPException(400, '请先在设置中配置有效格式的 DeepSeek 官方 Key')
    with db.tx() as c:
        found = c.execute('SELECT * FROM model_fallback_requests WHERE id=?', (rid,)).fetchone()
        if not found:
            raise HTTPException(404, '授权请求不存在')
        request = dict(found)
        if request['state'] == 'approved':
            return {'ok': True, 'job_id': request['retry_job_id'], 'reused': True}
        spec = db.loads(request['spec'], {})
        if request['state'] != 'pending' or not _valid(request, spec):
            raise HTTPException(409, '原任务已重试、暂停或原文已改变，请回原任务核对')
        route = research_protocol.resolve(spec['choice'])
        for key in ('rules', 'rule_hash', 'rule_version'):
            if key in spec['route']:
                route[key] = spec['route'][key]
        stamp = db.now()
        route['fallback'] = {'authorization_id': rid, 'authorized_at': stamp, 'source_job_id': request['job_id'],
                             'source_backend': request['source_backend'], 'source_model': request['source_model']}
        if spec.get('item_id'):
            from .intake_batches import enqueue
            item = _set_item_route(c, spec['item_id'], route, stamp)
            if spec.get('media_id'):
                # The raw extraction succeeded; only its failed reading layer is resumed.
                c.execute("UPDATE media_imports SET state='done',extraction_only=1,updated_at=? WHERE id=? AND state='failed' AND raw<>''", (stamp,spec['media_id']))
            enqueue(c, item)
            jid = c.execute('SELECT job_id FROM intake_items WHERE id=?', (item['id'],)).fetchone()[0]
        else:
            old = c.execute('SELECT payload FROM jobs WHERE id=?', (request['job_id'],)).fetchone()
            payload = db.loads(old['payload'], {})
            jid = c.execute('INSERT INTO jobs(kind,payload,created_at,updated_at) VALUES(?,?,?,?)', (spec['kind'], '{}',stamp,stamp)).lastrowid
            if spec['kind'] == 'media_import':
                payload['model_route'] = route
                c.execute("UPDATE media_imports SET state='queued',error='',job_id=?,updated_at=? WHERE id=?", (jid,stamp,spec['media_id']))
            else:
                payload.update(job_id=jid, route=route)
                c.execute("UPDATE benchmarks SET analysis_state='queued',analysis_error=NULL,pending_job_id=?,analysis_progress='等待已授权的 DeepSeek 研究' WHERE id=?", (jid,spec['benchmark_id']))
            c.execute('UPDATE jobs SET payload=? WHERE id=?', (db.dumps(payload),jid))
        c.execute("UPDATE model_fallback_requests SET state='approved',decided_at=?,retry_job_id=? WHERE id=?", (stamp,jid,rid))
    from . import jobs
    jobs.wake()
    return {'ok': True, 'job_id': jid}
