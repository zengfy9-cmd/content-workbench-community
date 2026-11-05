"""Send an approved local manifest to the existing workbench, never to the preview."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    data = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    base = args.manifest.resolve().parent
    data['body'] = (base / data.pop('body_path')).read_text(encoding='utf-8-sig')
    raw_images = [(base / p).read_bytes() for p in data.get('image_paths', [])]
    data['images'] = [{'name':Path(p).name,'base64':base64.b64encode(raw).decode()} for p,raw in zip(data.pop('image_paths', []),raw_images)]
    with httpx.Client(base_url='http://127.0.0.1:8765',headers={'Origin':'http://localhost:8765'},timeout=120,trust_env=False) as client:
        response = client.post('/api/codex/archive',json=data);response.raise_for_status()
        receipt = response.json()
        response = client.get(f"/api/drafts/{receipt['draft_id']}");response.raise_for_status()
        draft = response.json()
        assert draft['title']==data['title'], '标题读回不一致'
        expected = data['body']
        for i,raw in enumerate(raw_images,1):
            iid = receipt['image_ids'][i-1]
            image = client.get(f'/api/images/{iid}');image.raise_for_status()
            assert hashlib.sha256(image.content).digest()==hashlib.sha256(raw).digest(), '配图读回不一致'
            expected=expected.replace('{{image:%d}}'%i,f'![配图{i}](/api/images/{iid})')
        assert draft['body']==expected, '正文读回不一致'
        receipt['verified']=True
        args.manifest.with_suffix('.receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(receipt,ensure_ascii=False))


if __name__ == '__main__':
    main()
