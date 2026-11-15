"""Validation shared by the private chat API and the paired Codex executor."""
import base64
import binascii
import io
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

MAX_SIZE = 4 * 1024 * 1024
MAX_IMAGES = 2
FORMATS = {'PNG': ('image/png', '.png'), 'JPEG': ('image/jpeg', '.jpg'), 'WEBP': ('image/webp', '.webp')}


def decode(values):
    if not isinstance(values, list) or len(values) > MAX_IMAGES:
        raise ValueError('每次最多发送 2 张截图')
    images = []
    for value in values:
        try:
            if not isinstance(value, str) or len(value) > ((MAX_SIZE + 2) // 3) * 4:
                raise ValueError()
            raw = base64.b64decode(value, validate=True)
            if not raw or len(raw) > MAX_SIZE:
                raise ValueError()
            with Image.open(io.BytesIO(raw)) as image:
                mime, suffix = FORMATS[image.format]
                if image.width * image.height > 16_000_000 or getattr(image, 'is_animated', False):
                    raise ValueError()
                image.verify()
        except (ValueError, KeyError, OSError, binascii.Error, Image.DecompressionBombError, SyntaxError):
            raise ValueError('请使用有效的 PNG、JPEG 或 WebP 截图（每张不超过 4 MB，最多 1600 万像素）') from None
        images.append((raw, mime, suffix))
    return images


def run(native, prompt, images, **kwargs):
    if not images:
        return native(prompt, **kwargs)
    with TemporaryDirectory(prefix='cw-control-') as folder:
        paths = []
        for index, (raw, _, suffix) in enumerate(images, 1):
            path = Path(folder) / (str(index) + suffix)
            path.write_bytes(raw)
            paths.append(path)
        return native(prompt, attachments=paths, **kwargs)
