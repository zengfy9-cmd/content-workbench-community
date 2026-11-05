"""Check the exact staged or committed Git file set; never print matched secrets."""
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]
names = subprocess.check_output(['git','ls-files','-z'], cwd=root).decode('utf-8').split('\0')
forbidden_parts = {'.venv','node_modules','dist','.verification','__pycache__','xhs-browser','data'}
forbidden_names = {'secrets.json','settings.json','setup-code.txt','auth.json','cookies.json','storage-state.json'}
patterns = [
    r'gh[pousr]_[A-Za-z0-9]{25,}',
    r'github_pat_[A-Za-z0-9_]{25,}',
    r'sk-[A-Za-z0-9_-]{25,}',
    r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    r'cli_[a-z0-9]{14,}',
    r'[A-Z]:[/\\](?:Users|AI-Private|AI-Skills-Library)',
]
issues = []
count = 0
for name in filter(None,names):
    path = Path(name)
    if set(path.parts) & forbidden_parts or path.name in forbidden_names or path.suffix.lower() in {'.db','.sqlite','.sqlite3','.log','.pem','.key'}:
        issues.append((name,'excluded file type'))
    raw = (root/path).read_bytes()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        issues.append((name,'unexpected binary file; review before publication'))
        continue
    if any(re.search(pattern,text) for pattern in patterns):
        issues.append((name,'credential or private path pattern'))
    count += 1
for path,reason in issues:
    print(f'FAIL: {path}: {reason}')
if issues:
    raise SystemExit(1)
print(f'PASS: {count} versioned text files; no forbidden artifacts or supported credential/private-path patterns. Manual content review is still required.')
