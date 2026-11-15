"""Run the optional local image executor and its private SSH reverse forward."""
import json
import os
import subprocess
import sys
import time

from .config import data_dir


def main():
    conf=json.loads((data_dir()/'cloud-link.json').read_text(encoding='utf-8'))
    host=conf['ssh_host'];user=conf['ssh_user'];key=conf['ssh_key'];socket=conf['remote_socket']
    if not all(isinstance(v,str) for v in (host,user,key,socket)) or not socket.startswith('/opt/content-workbench/gateway/'):
        raise RuntimeError('执行器连接配置无效')
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10',
         '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3','-i',key,user+'@'+host]
    # The socket is owned by this deployment; no public gateway port is opened.
    cleanup="python3 -c \"import socket,pathlib; p=pathlib.Path('/opt/content-workbench/gateway/codex.sock'); s=socket.socket(socket.AF_UNIX); c=s.connect_ex(str(p)); s.close(); p.unlink(missing_ok=True) if c else None\""
    executor=None;tunnel=None;inbox=None
    try:
        while True:
            if inbox is None or inbox.poll() is not None:
                inbox=subprocess.Popen([sys.executable,'-m','content_workbench.mobile_forwarder'],env=os.environ.copy(),creationflags=flags)
            if executor is None or executor.poll() is not None:
                executor=subprocess.Popen([sys.executable,'-m','content_workbench.local_executor'],env=os.environ.copy(),creationflags=flags)
            if tunnel is None or tunnel.poll() is not None:
                try:subprocess.run([*ssh,cleanup],capture_output=True,timeout=20,creationflags=flags)
                except (OSError,subprocess.TimeoutExpired):time.sleep(10);continue
                tunnel=subprocess.Popen([*ssh,'-N','-o','ExitOnForwardFailure=yes','-R',socket+':127.0.0.1:8789','-L','127.0.0.1:18768:127.0.0.1:8768'],
                                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
            time.sleep(10)
    finally:
        for process in (executor,tunnel,inbox):
            if process and process.poll() is None:process.terminate()

if __name__=='__main__':main()
