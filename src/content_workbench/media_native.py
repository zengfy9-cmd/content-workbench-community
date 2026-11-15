"""Portable media steps without the optional personal XiaoD installation."""
import json
import os
import sys
from pathlib import Path
from . import transcription
from .xiaod_bridge import reading_source

def main():
    mode=sys.argv[1];base=Path(sys.argv[2]);request=json.loads((base/'request.json').read_text(encoding='utf-8'))
    if mode=='transcribe':
        os.environ.setdefault('CW_WHISPER_MODEL',request.get('asr_model') or 'base')
        result=transcription.transcribe(base/'audio.wav',initial_prompt='以下是普通话视频中的口述内容。保留原话，不做总结。')
        (base/'asr-result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        (base/'raw.txt').write_text(result['text'],encoding='utf-8')
    elif mode=='transcribe-checked':
        os.environ.setdefault('CW_WHISPER_MODEL',request.get('asr_model') or 'base')
        result,attempts=transcription.checked_speech(base/'audio.wav')
        (base/'asr-attempts.json').write_text(json.dumps(attempts,ensure_ascii=False),encoding='utf-8')
        if not result:
            return 2
        (base/'asr-result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        (base/'checked-asr.txt').write_text(result['text'],encoding='utf-8')
    elif mode=='clean':
        (base/'clean.txt').write_text(reading_source(base),encoding='utf-8')
    elif mode=='quality':
        article=(base/'article.md').read_text(encoding='utf-8')
        (base/'quality.json').write_text(json.dumps(['content is unusually short'] if len(article)<100 else []),encoding='utf-8')
    else:raise RuntimeError('此平台尚无可用的下载步骤，请上传原视频；没有生成虚构转写。')

if __name__=='__main__':sys.exit(main())
