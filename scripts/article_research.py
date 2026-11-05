"""Codex bridge: prepare a source envelope, or import a reviewed research JSON.

No model call, publishing or credential export is performed by this script.
"""
import argparse
import json
import sys
from pathlib import Path

import httpx


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url')
    parser.add_argument('--benchmark',type=int)
    parser.add_argument('--research',type=Path)
    args=parser.parse_args()
    with httpx.Client(base_url='http://127.0.0.1:8765',headers={'Origin':'http://localhost:8765'},timeout=240,trust_env=False) as client:
        def request(method,path,**kwargs):
            r=client.request(method,path,**kwargs);r.raise_for_status();return r.json()
        bid=args.benchmark
        if args.url:
            source=request('POST','/api/benchmarks/read',json={'url':args.url})
            source['source']='web'
            source=request('POST','/api/benchmarks',json=source)
            bid=source['id']
        if not bid:
            parser.error('需要 --url 或 --benchmark')
        source=request('GET',f'/api/benchmarks/{bid}')
        if args.research:
            payload=json.loads(args.research.read_text(encoding='utf-8-sig'))
            result=request('POST',f'/api/benchmarks/{bid}/research/import',json=payload)
            print(json.dumps({'id':bid,'analysis_id':result['analysis']['id'],'url':f'http://localhost:8765/#/benchmarks/{bid}'},ensure_ascii=False))
        else:
            protocol=request('GET','/api/research/options')
            protocol={k:protocol[k] for k in ('rule_version','rule_hash','rules')}
            print(json.dumps({'benchmark_id':bid,'body_hash':source['body_hash'],'title':source['title'],'url':source['url'],'author':source['author'],'read_count':source.get('read_count'),'protocol':protocol,'paragraphs':[{'key':p['key'],'text':p['markdown']} for p in source['passages']]},ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:
        main()
    except (httpx.HTTPError,ValueError) as exc:
        print('文章读取或分析导入失败，请检查工作台状态、原文版本与 JSON 格式。',file=sys.stderr)
        sys.exit(1)
