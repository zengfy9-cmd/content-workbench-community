"""Online SQLite snapshots with their referenced pictures; complete snapshots only."""
from __future__ import annotations

import datetime
import json
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from .config import data_dir


def snapshot():
    root=data_dir();destination=root/'cloud-backups';destination.mkdir(exist_ok=True)
    source=sqlite3.connect(root/'workbench.db');source.row_factory=sqlite3.Row
    # No file-by-file copying of a running WAL database.
    with tempfile.TemporaryDirectory(prefix='.pending-',dir=destination) as temporary:
        stage=Path(temporary)
        with closing(sqlite3.connect(stage/'workbench.db')) as output:source.backup(output)
        source.close()
        copied=[]
        with closing(sqlite3.connect(stage/'workbench.db')) as db:
            assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
            files=[('images/'+r[0]) for r in db.execute('SELECT file FROM images')]
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='article_image_tasks'").fetchone():
                for row in db.execute('SELECT artifacts FROM article_image_tasks'):
                    files.extend('article-images/'+a['file'] for a in json.loads(row[0] or '[]'))
        for name in set(files):
            file=(root/name).resolve()
            if not file.is_relative_to(root.resolve()) or not file.is_file():raise RuntimeError('备份附件缺失')
            target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,target);copied.append(name)
        for name in ['settings.json','article-images/visual-profile.json']:
            if (root/name).is_file():
                (stage/name).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/name,stage/name)
        if (root/'article-images/references').is_dir():shutil.copytree(root/'article-images/references',stage/'article-images/references')
        if (root/'context').is_dir():shutil.copytree(root/'context',stage/'context')
        (stage/'complete.json').write_text(json.dumps({'image_files':len(copied),'database':'ok'}),encoding='utf-8')
        stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        completed=destination/stamp
        stage.rename(completed)
    # Keep 12 recent recovery points and the first point from each of 14 days.
    complete=sorted(p for p in destination.iterdir() if p.is_dir() and (p/'complete.json').is_file())
    keep=set(complete[-12:]);days={}
    for p in complete:days.setdefault(p.name[:8],p)
    keep.update(list(days.values())[-14:])
    for p in complete:
        if p not in keep and p.parent.resolve()==destination.resolve():shutil.rmtree(p)
    return {'path':completed.name,'image_files':len(copied)}
