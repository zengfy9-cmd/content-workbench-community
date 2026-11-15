"""Single-owner cloud login. Passwords never leave the HTTPS form in plaintext logs."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time

from fastapi import HTTPException
from . import db
from .config import clear_setup_code, ensure_setup_code

HASH_LOCK = threading.BoundedSemaphore(2)


def setup():
    with db.tx() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS owner_account(
            id INTEGER PRIMARY KEY CHECK(id=1),username TEXT NOT NULL,
            salt TEXT NOT NULL,password_hash TEXT NOT NULL,created_at REAL NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS login_limits(
            bucket TEXT PRIMARY KEY,attempts INTEGER NOT NULL,started_at REAL NOT NULL)''')


def registered():
    return bool(db.row('SELECT 1 FROM owner_account WHERE id=1'))


def _rate(request, action):
    # Cloud requests arrive from the local reverse proxy. A global bucket is
    # deliberate: do not trust arbitrary forwarded client IP headers.
    bucket=action+':owner';stamp=time.time()
    with db.tx() as conn:
        row=conn.execute('SELECT * FROM login_limits WHERE bucket=?',(bucket,)).fetchone()
        if not row or stamp-row['started_at']>900:
            conn.execute('INSERT OR REPLACE INTO login_limits VALUES(?,?,?)',(bucket,1,stamp))
        else:
            if row['attempts']>=20:raise HTTPException(429,'尝试次数过多，请15分钟后重试')
            conn.execute('UPDATE login_limits SET attempts=attempts+1 WHERE bucket=?',(bucket,))


def _hash(password,salt):
    if not HASH_LOCK.acquire(blocking=False):raise HTTPException(429,'登录请求繁忙，请稍后重试')
    try:return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=32768,r=8,p=1,maxmem=64*1024*1024).hex()
    finally:HASH_LOCK.release()


def register(request, body):
    _rate(request,'register')
    if registered():raise HTTPException(409,'此工作台已创建账号，请直接登录')
    code=body.get('setup_code');username=body.get('username');password=body.get('password')
    if not isinstance(code,str) or not hmac.compare_digest(code.strip(),ensure_setup_code()):
        raise HTTPException(403,'初始化码不正确')
    if not isinstance(username,str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{3,80}',username):
        raise HTTPException(400,'账号请用3至80位字母、数字或 _ . @ -')
    if not isinstance(password,str) or not 12<=len(password)<=256:
        raise HTTPException(400,'密码需要12至256个字符')
    salt=secrets.token_hex(16);hashed=_hash(password,salt)
    with db.tx() as conn:
        if conn.execute('SELECT 1 FROM owner_account WHERE id=1').fetchone():raise HTTPException(409,'账号已创建，请登录')
        conn.execute('INSERT INTO owner_account VALUES(1,?,?,?,?)',(username.lower(),salt,hashed,time.time()))
        conn.execute('DELETE FROM login_limits')
    clear_setup_code()


def login(request,body):
    _rate(request,'login')
    username=body.get('username');password=body.get('password')
    if not isinstance(username,str) or not isinstance(password,str) or len(username)>80 or len(password)>256:
        raise HTTPException(401,'账号或密码不正确')
    row=db.row('SELECT * FROM owner_account WHERE id=1')
    salt=row['salt'] if row else '00'*16
    candidate=_hash(password,salt)
    if not row or not hmac.compare_digest(username.lower(),row['username']) or not hmac.compare_digest(candidate,row['password_hash']):
        raise HTTPException(401,'账号或密码不正确')
    with db.tx() as conn:conn.execute("DELETE FROM login_limits WHERE bucket='login:owner'")
