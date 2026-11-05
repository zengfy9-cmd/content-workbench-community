"""Read public reference articles without executing their HTML or using account cookies."""

from __future__ import annotations

import http.client
import json
from html import unescape
from html import escape
import ipaddress
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

MAX_BYTES = 8 * 1024 * 1024  # WeChat pages include several MB of inline scripts outside the article.
MAX_BODY = 200_000


class ReferenceError(ValueError):
    pass


class _NetworkError(ReferenceError):
    pass


def article_url(value: str) -> str:
    value = value.strip()
    if len(value) > 4096 or re.search(r"[\s\x00-\x1f\x7f]", value):
        raise ReferenceError("请填写完整的 http 或 https 原文链接")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise ReferenceError("原文链接格式不正确") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ReferenceError("请填写完整的 http 或 https 原文链接")
    if port not in (None, 80, 443):
        raise ReferenceError("只支持普通网页链接")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _addresses(host: str, port: int) -> list[str]:
    try:
        addresses = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    except OSError as exc:
        raise ReferenceError("无法连接原文网站，可以改为粘贴原文") from exc
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ReferenceError("只支持公开网站，不能读取本机或内网地址")
    return addresses


def _download(url: str) -> tuple[str, str]:
    # A dropped/slow public connection gets one new attempt, never a partial page.
    for attempt in range(2):
        try:
            return _download_once(url, attempt)
        except _NetworkError as exc:
            if attempt:
                raise ReferenceError(str(exc) + "；已自动重试，请稍后重试这条链接") from exc
    raise AssertionError('unreachable')


def _download_once(url: str, attempt: int = 0) -> tuple[str, str]:
    deadline = time.monotonic() + 90
    for _ in range(5):
        url = article_url(url)
        parts = urlsplit(url)
        host = parts.hostname.encode("idna").decode("ascii")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = _addresses(host, port)
        # On retry, try another validated edge first, keeping both IP families.
        if attempt and len(addresses) > 1:
            addresses = addresses[1:] + addresses[:1]

        # Pin the connection to the validated IPs; HTTPS still checks the original hostname.
        def connect(_address, timeout=30, source_address=None):
            last = None
            for address in addresses:
                try:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('download deadline exceeded')
                    sock = socket.create_connection((address, port), min(6, remaining), source_address)
                    sock.settimeout(min(timeout, remaining))
                    return sock
                except OSError as exc:
                    last = exc
            raise last or OSError("connection failed")

        cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = cls(host, port=port, timeout=30)
        conn._create_connection = connect
        try:
            path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
            if parts.query:
                path += "?" + quote(parts.query, safe="%/?@!$&'()*+,;=:-._~")
            conn.request("GET", path, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
                "Accept-Encoding": "identity",
                "Connection": "close",
            })
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise ReferenceError("原文网站跳转失败，请粘贴原文")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ReferenceError(f"原文网站返回 {response.status}，可以打开网页后粘贴原文")
            content_type = response.getheader("Content-Type", "").lower()
            if not any(kind in content_type for kind in ("text/html", "application/xhtml+xml", "text/plain")):
                raise ReferenceError("该链接不是可读取的文章网页，请粘贴原文")
            if response.getheader("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise ReferenceError("该网站的页面暂时无法读取，请粘贴原文")
            chunks, size = [], 0
            while chunk := response.read1(65536):
                if time.monotonic() > deadline:
                    raise TimeoutError('download deadline exceeded')
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ReferenceError("原文网页过大，请复制需要参考的正文")
                chunks.append(chunk)
            data = b"".join(chunks)
            expected = response.getheader('Content-Length')
            if expected and expected.isdecimal() and int(expected) != size:
                raise _NetworkError('原文下载未完成，未保存不完整正文')
            charset = re.search(r"charset=[\"']?([\w-]+)", content_type)
            encoding = charset.group(1) if charset else "utf-8"
            try:
                return data.decode(encoding, errors="replace"), url
            except LookupError:
                return data.decode("utf-8", errors="replace"), url
        except ssl.SSLCertVerificationError as exc:
            raise ReferenceError('原文网站证书验证失败，未读取网页') from exc
        except TimeoutError as exc:
            raise _NetworkError('原文下载超时，未保存不完整正文') from exc
        except (OSError, http.client.HTTPException) as exc:
            raise _NetworkError('原文连接中断，未保存不完整正文') from exc
        except UnicodeError as exc:
            raise ReferenceError('原文编码暂时无法读取，请粘贴正文') from exc
        finally:
            conn.close()
    raise ReferenceError("原文网页跳转过多，请粘贴原文")


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        if len(self.stack) > 200:
            raise ReferenceError("网页结构过于复杂，请粘贴原文正文")
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _link(value: str, base: str) -> str:
    try:
        value = urljoin(base, value or "")
        parts = urlsplit(value)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return ""
    if parts.hostname.lower() in {"localhost", "localhost.localdomain"} or parts.hostname.endswith(".local"):
        return ""
    try:
        if not ipaddress.ip_address(parts.hostname).is_global:
            return ""
    except ValueError:
        pass
    return value.replace("<", "%3C").replace(">", "%3E").replace(" ", "%20")


def _render(node, base: str) -> str:
    if isinstance(node, str):
        return re.sub(r"([\\`*_[\]<>])", r"\\\1", re.sub(r"\s+", " ", node))
    if node.tag in {"script", "style", "noscript", "nav", "footer", "form", "iframe", "button", "svg", "head"}:
        return ""
    if node.attrs.get("hidden") is not None or "display:none" in node.attrs.get("style", "").replace(" ", "").lower():
        return ""
    if node.tag == "img":
        src = _link(node.attrs.get("data-src") or node.attrs.get("data-original") or node.attrs.get("src", ""), base)
        return f"\n\n![原文配图](<{src}>)\n\n" if src else ""
    text = "".join(_render(child, base) for child in node.children)
    if re.fullmatch(r"h[1-6]", node.tag):
        return f"\n\n{'#' * int(node.tag[1])} {text.strip()}\n\n"
    if node.tag == "br":
        return "\n"
    if node.tag == "li":
        return f"\n- {text.strip()}\n"
    if node.tag in {"p", "div", "section", "article", "main", "ul", "ol", "blockquote", "pre", "figure", "tr"}:
        return f"\n\n{text.strip()}\n\n"
    if node.tag in {"td", "th"}:
        return text + " | "
    if node.tag in {"strong", "b"} and text.strip() and "\n" not in text:
        return f"**{text.strip()}**"
    if node.tag == "a":
        href = _link(node.attrs.get("href", ""), base)
        if href and text.strip() and "\n" not in text:
            return f"[{text.strip()}](<{href}>)"
    return text


def _plain(node) -> str:
    return node if isinstance(node, str) else "".join(_plain(child) for child in node.children)


def safe_original(html: str, url: str = '') -> str:
    """Keep readable source formatting, never scripts, forms, remote CSS or CSS URLs."""
    parser = ArticleParser()
    parser.feed(html[:1500000])
    content = next((n for n in parser.nodes if n.attrs.get('id') == 'js_content'), parser.root)
    def render(node):
        if isinstance(node, str):
            return escape(node)
        if node.tag in {'script','style','head','iframe','form','input','button','svg','nav','footer','noscript'}:
            return ''
        if 'hidden' in node.attrs or 'display:none' in node.attrs.get('style','').replace(' ','').lower():
            return ''
        tag = node.tag if node.tag in {'p','div','section','article','span','h1','h2','h3','h4','strong','b','em','i','u','s','blockquote','ul','ol','li','pre','code','table','thead','tbody','tr','td','th','br','hr','img','a'} else 'div'
        attrs = ''
        if tag == 'img':
            src = _link(node.attrs.get('data-src') or node.attrs.get('src',''),url)
            return '<img src="'+escape(src,quote=True)+'" referrerpolicy="no-referrer" loading="lazy">' if src else ''
        if tag == 'a':
            href = _link(node.attrs.get('href',''),url)
            if href:
                attrs += ' href="'+escape(href,quote=True)+'" target="_blank" rel="noopener noreferrer"'
        styles=[]
        for pair in node.attrs.get('style','').split(';'):
            key,_,value=pair.partition(':');key=key.strip().lower();value=value.strip()
            if not re.search(r'url|expression|javascript|@import',value,re.I) and key in {'color','background-color','font-size','font-weight','font-style','text-decoration','text-align','line-height','letter-spacing','margin-top','margin-bottom','padding-top','padding-bottom','border-left'} and re.fullmatch(r'[a-zA-Z0-9#(),.%\s-]{1,100}',value):
                styles.append(key+':'+value)
        if styles:
            attrs += ' style="'+escape(';'.join(styles),quote=True)+'"'
        return '<'+tag+attrs+'>'+''.join(render(c) for c in node.children)+('' if tag in {'br','hr'} else '</'+tag+'>')
    return render(content)


def _author_name(value) -> str:
    if isinstance(value, list):
        return "、".join(filter(None, (_author_name(v) for v in value)))[:120]
    if isinstance(value, dict):
        return _author_name(value.get("name", ""))
    if not isinstance(value, str) or re.match(r"https?://", value):
        return ""
    return re.sub(r"\s+", " ", unescape(value)).strip()[:120]


def _metadata(parser: ArticleParser, host: str) -> dict:
    meta = {str(n.attrs.get("property") or n.attrs.get("name") or "").lower(): n.attrs.get("content", "") for n in parser.nodes if n.tag == "meta"}
    author, account = "", ""
    if host == "mp.weixin.qq.com":
        byline = next((n for n in parser.nodes if n.attrs.get("id") == "js_author_name"), None)
        nick = next((n for n in parser.nodes if n.attrs.get("id") == "js_name"), None)
        author = _author_name(_plain(byline)) if byline else ""
        account = _author_name(_plain(nick)) if nick else ""
    author = author or _author_name(meta.get("author") or meta.get("article:author") or "")
    if not author:
        for node in parser.nodes:
            if node.tag != "script" or node.attrs.get("type", "").lower() != "application/ld+json":
                continue
            raw = _plain(node)
            if len(raw) > 256000:
                continue
            try:
                values = json.loads(raw)
                values = values if isinstance(values, list) else [values]
                for value in values:
                    if not isinstance(value, dict):
                        continue
                    for item in [value, *value.get("@graph", [])]:
                        if isinstance(item, dict):
                            author = _author_name(item.get("author"))
                            if author:
                                break
                    if author:
                        break
            except (ValueError, TypeError):
                continue
            if author:
                break
    tags = []
    for name in ("keywords", "news_keywords", "article:tag"):
        tags.extend(re.split(r"[,，;；|]", meta.get(name, "")))
    return {"author": author or account, "account": account,
            "tags": list(dict.fromkeys(t.strip() for t in tags if 0 < len(t.strip()) <= 40))[:8]}


def extract_article(html: str, url: str) -> dict:
    parser = ArticleParser()
    parser.feed(html)
    nodes = parser.nodes
    host = (urlsplit(url).hostname or "").lower()
    metadata = _metadata(parser, host)
    meta = {n.attrs.get("property", n.attrs.get("name", "")): n.attrs.get("content", "") for n in nodes if n.tag == "meta"}
    content = next((n for n in nodes if n.attrs.get("id") == "js_content"), None)
    # Ordinary WeChat long articles also carry picture_page_info_list. Their
    # visible body is authoritative; description + gallery is only a fallback
    # for genuine picture posts without an article body.
    has_wechat_body = content is not None and bool(_render(content, url).strip())
    if host == "mp.weixin.qq.com" and not has_wechat_body:
        match = re.search(r"\bpicture_page_info_list\s*:\s*(\[)", html)
        if match:
            pictures = _balanced(html, match.start(1))
            urls = []
            # Each top-level picture object starts with its own URL; nested watermark/cover URLs are excluded.
            for item in _array_objects(pictures):
                image = re.search(r"\bcdn_url\s*:\s*'((?:\\.|[^'\\])*)'", item)
                if image:
                    src = _link(_js_text(image.group(1)), url)
                    if src and src not in urls:
                        urls.append(src)
            if urls:
                description = _js_text(meta.get("og:description", ""))
                description_parser = ArticleParser()
                description_parser.feed(description.replace('\n', '<br>'))
                body = _render(description_parser.root, url).strip() + "\n\n" + "\n\n".join(f"![原文配图](<{src}>)" for src in urls)
                if len(body) > MAX_BODY:
                    raise ReferenceError("正文过长，未截断保存；请粘贴需要参考的部分")
                return {"title": _js_text(meta.get("og:title", "公众号图片帖"))[:300], "url": url, "body": body.strip(), **metadata}
    if host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com"):
        match = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{)", html)
        if match:
            raw = _balanced(html, match.start(1))
            # Replace JS undefined only outside strings; never execute page scripts.
            raw = re.sub(r'"(?:\\.|[^"\\])*"|\bundefined\b', lambda m: "null" if m.group() == "undefined" else m.group(), raw)
            try:
                state = json.loads(raw)
                details = state.get("note", {}).get("noteDetailMap", {})
                note_id = urlsplit(url).path.rstrip("/").split("/")[-1]
                note = details.get(note_id, {}).get("note", {})
                if note:
                    parts = [str(note.get("desc") or "")]
                    for picture in note.get("imageList") or []:
                        src = _link(picture.get("urlDefault") or picture.get("url") or "", url)
                        if src:
                            parts.append(f"![笔记配图](<{src}>)")
                    body = "\n\n".join(parts).strip()
                    if body:
                        return {"title": str(note.get("title") or "小红书笔记")[:300], "url": url, "body": body[:MAX_BODY],
                                **metadata, "author": _author_name((note.get("user") or {}).get("nickname")) or metadata["author"]}
            except (ValueError, AttributeError, TypeError):
                pass
        raise ReferenceError("未读到小红书笔记，请使用含分享参数的完整链接；如果需要登录，请打开原网页后导入正文或图片")
    if content is None:
        content = next((n for n in nodes if n.tag == "article"), None)
    if content is None:
        content = next((n for n in nodes if n.tag == "main"), None)
    if content is None:
        content = next((n for n in nodes if n.tag == "body"), parser.root)
    title = next((n for n in nodes if n.attrs.get("id") == "activity-name"), None)
    if title is None:
        title = next((n for n in nodes if n.tag == "h1"), None)
    if title is None:
        title = next((n for n in nodes if n.tag == "title"), None)

    def plain(node):
        return node if isinstance(node, str) else "".join(plain(child) for child in node.children)

    title_text = re.sub(r"\s+", " ", plain(title)).strip() if title else urlsplit(url).hostname
    body = re.sub(r"\n[ \t]+", "\n", _render(content, url))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if len(body) < 60 or any(marker in title_text for marker in ("环境异常", "安全验证", "访问验证")):
        raise ReferenceError("未读到文章正文，网站可能需要登录或验证。请打开原网页，复制正文后粘贴")
    if len(body) > MAX_BODY:
        raise ReferenceError("正文过长，请粘贴需要参考的部分")
    count = None
    reading = re.search(r'(?:["\x27]read_num["\x27]\s*:\s*["\x27]?|id=["\x27]readNum["\x27][^>]*>\s*)([1-9]\d{0,9})', html)
    if reading:
        count = int(reading.group(1))
    return {"title": title_text[:300], "url": url, "body": body, "original_html": safe_original(html, url), "read_count": count, **metadata}


def read_article(url: str) -> dict:
    # Mobile apps copy a whole share message, not just a URL.
    if not url.strip().startswith(("http://", "https://")) or re.search(r"\s", url.strip()):
        matches = re.findall(r'https?://[^\s<>"）]+', url)
        if len(matches) != 1:
            raise ReferenceError("请粘贴一条文章链接或只包含一个链接的分享文案")
        url = matches[0].rstrip("，。；)]")
    url = article_url(url)
    host = urlsplit(url).hostname or ""
    if host in {"scys.com", "www.scys.com"}:
        raise ReferenceError("生财文章需要通过生财 MCP 读取，当前工作台尚未连接；可以由 Codex 读取后导入，或先打开原网页")
    if host == "zsxq.com" or host.endswith(".zsxq.com"):
        raise ReferenceError("知识星球文章需要对应社群的登录权限，请打开原网页后导入正文；生财有术文章请使用 scys.com 链接")
    html, final_url = _download(url)
    return extract_article(html, final_url)


def _js_text(value: str) -> str:
    value = re.sub(r"\\x([0-9a-fA-F]{2})|\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1) or m.group(2), 16)), value)
    return unescape(value.replace(r"\n", "\n").replace(r"\/", "/").replace(r"\'", "'"))


def _balanced(text: str, start: int) -> str:
    depth, quote_char, escaped = 0, "", False
    for i in range(start, len(text)):
        char = text[i]
        if quote_char:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote_char:
                quote_char = ""
        elif char in "\"'":
            quote_char = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return ""


def _array_objects(text: str):
    pos = 1
    while pos < len(text):
        if text[pos] == "{":
            item = _balanced(text, pos)
            if not item:
                return
            yield item
            pos += len(item)
        else:
            pos += 1
