"""Reference reading never changes the article being written or starts model jobs."""

import socket

import pytest

from content_workbench import db, references
from test_app import ORIGIN, client, login

H = {"Origin": ORIGIN}
URL = "https://mp.weixin.qq.com/s/example"


def test_wechat_long_article_prefers_complete_body_over_picture_metadata():
    html = '''<meta property="og:title" content="完整长文">
    <meta property="og:description" content="这只是分享摘要，不是完整正文。">
    <h1 id="activity-name">完整长文</h1><span id="js_name">原作者</span>
    <div id="js_content"><p>真实第一段，必须保留这段文字并按原文顺序展示。</p>
    <img data-src="https://example.com/inline-one.jpg">
    <h2>一、实际步骤</h2><p>文章中间的具体操作、条件与例子，不能被图片帖分支跳过。</p>
    <img data-src="https://example.com/inline-two.jpg">
    <p>真实结尾段，含验证结果和边界说明，必须一直读到文章最后。</p></div>
    <script>var info={picture_page_info_list:[{cdn_url:'https://example.com/gallery.jpg'}]};</script>'''
    article = references.extract_article(html, URL)
    body = article['body']
    assert article['author'] == '原作者' and article['title'] == '完整长文'
    assert body.index('真实第一段') < body.index('inline-one.jpg') < body.index('实际步骤')
    assert body.index('实际步骤') < body.index('文章中间') < body.index('inline-two.jpg') < body.index('真实结尾段')
    assert '分享摘要' not in body and 'gallery.jpg' not in body


def test_wechat_picture_post_with_empty_body_still_keeps_caption_and_gallery():
    html = '''<meta property="og:title" content="图片帖"><meta property="og:description" content="图片帖完整说明">
    <div id="js_content"> </div><script>var info={picture_page_info_list:[{cdn_url:'https://example.com/one.jpg'},{cdn_url:'https://example.com/two.jpg'}]};</script>'''
    body = references.extract_article(html, URL)['body']
    assert body.index('完整说明') < body.index('one.jpg') < body.index('two.jpg')


def test_wechat_picture_post_does_not_silently_truncate(monkeypatch):
    monkeypatch.setattr(references, 'MAX_BODY', 80)
    html = '<meta property="og:description" content="' + '完整原文' * 50 + '''"><script>var info={picture_page_info_list:[{cdn_url:'https://example.com/one.jpg'}]};</script>'''
    with pytest.raises(references.ReferenceError, match='未截断保存'):
        references.extract_article(html, URL)


def test_wechat_extract_keeps_body_order_and_lazy_images():
    html = '''<html><head><title>网页标题</title><script>doNotRun()</script></head><body>
    <h1 id="activity-name"> 对标文章的标题 </h1><nav>不要采集菜单</nav>
    <div id="js_content"><h2>第一节</h2><p>这是足够长的第一段原文，用来对照写作时的结构与表达，保留作者原有的论证。</p>
    <img data-src="https://mmbiz.qpic.cn/image.jpg" src="placeholder.jpg" onerror="evil()">
    <p>第二段继续讲清楚真实任务的步骤和结果，保留完整正文，不能用摘要替换。</p>
    <script>evil()</script><div style="display: none">隐藏广告</div></div><footer>底部菜单</footer></body></html>'''
    article = references.extract_article(html, URL)
    assert article["title"] == "对标文章的标题"
    body = article["body"]
    assert body.index("第一节") < body.index("第一段") < body.index("image.jpg") < body.index("第二段")
    assert "evil" not in body and "隐藏广告" not in body and "底部菜单" not in body
    assert "placeholder" not in body and "onerror" not in body


def test_extract_generic_article_and_ignore_unsafe_links():
    html = '<body><nav>菜单</nav><article><h1>参考</h1><p>' + '这是原文。' * 30 + '</p><a href="javascript:alert(1)">文字</a><img src="http://127.0.0.1/private"><a href="/next">下一页</a></article></body>'
    result = references.extract_article(html, "https://example.org/article")
    assert "菜单" not in result["body"] and "javascript" not in result["body"]
    assert "127.0.0.1" not in result["body"] and "https://example.org/next" in result["body"]


def test_blocked_page_is_not_saved_as_an_article():
    with pytest.raises(references.ReferenceError, match="未读到文章正文"):
        references.extract_article('<html><title>环境异常</title><body>' + '请验证身份' * 30 + '</body></html>', URL)


@pytest.mark.parametrize("url", ["file:///secret", "https://user:password@example.org/", "https://example.org:8000/", "javascript:alert(1)", "http://[bad", "https://example.org/\nHeader:x"])
def test_reject_non_article_urls(url):
    with pytest.raises(references.ReferenceError):
        references.article_url(url)


@pytest.mark.parametrize("ip", ["127.0.0.1", "192.168.1.3", "169.254.169.254", "::1", "::ffff:127.0.0.1"])
def test_reject_private_destinations(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))])
    with pytest.raises(references.ReferenceError, match="本机或内网"):
        references._addresses("example.org", 443)


def test_download_pins_ip_and_validates_redirect(monkeypatch):
    monkeypatch.setattr(references, "_addresses", lambda host, port: ["93.184.216.34"] if host == "example.org" else (_ for _ in ()).throw(references.ReferenceError("本机或内网")))
    calls = []

    class Response:
        status = 302

        def getheader(self, name, default=None):
            return "http://127.0.0.1/private" if name == "Location" else default

    class Connection:
        def __init__(self, host, **kwargs):
            assert host == "example.org"

        def request(self, method, path, headers):
            self._create_connection(("example.org", 443))
            assert "Cookie" not in headers and "Authorization" not in headers

        def getresponse(self): return Response()
        def close(self): pass

    monkeypatch.setattr(references.http.client, "HTTPSConnection", Connection)
    class Sock:
        def settimeout(self, timeout): pass
    monkeypatch.setattr(socket, "create_connection", lambda address, *a: calls.append(address) or Sock())
    with pytest.raises(references.ReferenceError, match="本机或内网"):
        references._download("https://example.org/article")
    assert calls == [("93.184.216.34", 443)]


def test_download_retries_timed_out_body_from_start_without_saving_partial(monkeypatch):
    monkeypatch.setattr(references, '_addresses', lambda *a: ['93.184.216.34', '93.184.216.35'])
    calls, connected, timeouts = [], [], []
    class Sock:
        def settimeout(self, value): timeouts.append(value)
    def connect(address, timeout, source):
        connected.append(address); assert timeout <= 6
        return Sock()
    monkeypatch.setattr(socket, 'create_connection', connect)
    class Response:
        status = 200
        def __init__(self, first): self.first=first; self.n=0
        def getheader(self, name, default=None):
            return {'Content-Type':'text/html; charset=utf-8','Content-Length':'8'}.get(name, default)
        def read1(self, size):
            self.n += 1
            if self.first:
                if self.n == 1: return b'partial'
                raise TimeoutError('slow response')
            return [b'full', b'body', b''][self.n-1]
        def read(self, size): pytest.fail('read() waits to fill a large buffer; use read1()')
    class Connection:
        def __init__(self, host, **kw): self.timeout=kw['timeout']; self.closed=False
        def request(self, method, path, headers):
            assert headers['Connection'] == 'close' and 'Cookie' not in headers
            self._create_connection(('example.org',443), self.timeout)
            calls.append(self)
        def getresponse(self): return Response(len(calls)==1)
        def close(self): self.closed=True
    monkeypatch.setattr(references.http.client, 'HTTPSConnection', Connection)
    body, url = references._download('https://example.org/a')
    assert body == 'fullbody' and url == 'https://example.org/a'
    assert len(calls) == 2 and all(c.closed for c in calls)
    assert connected == [('93.184.216.34',443),('93.184.216.35',443)]
    assert timeouts == [30,30]


def test_truncated_content_length_does_not_become_a_successful_article(monkeypatch):
    monkeypatch.setattr(references, '_addresses', lambda *a: ['93.184.216.34'])
    calls=[]
    class Response:
        status=200
        def getheader(self, name, default=None): return {'Content-Type':'text/html','Content-Length':'999'}.get(name,default)
        def read1(self, size):
            if hasattr(self,'read_once'):return b''
            self.read_once=True;return b'<p>incomplete article</p>'
    class Connection:
        def __init__(self, *a, **k): calls.append(self)
        def request(self, *a, **k): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(references.http.client,'HTTPSConnection',Connection)
    with pytest.raises(references.ReferenceError,match='下载未完成'):
        references._download('https://example.org/a')
    assert len(calls)==2


@pytest.mark.parametrize('problem', ['denied','certificate','oversize'])
def test_download_does_not_retry_permission_certificate_or_size_failures(monkeypatch,problem):
    import ssl
    monkeypatch.setattr(references, '_addresses', lambda *a: ['93.184.216.34'])
    monkeypatch.setattr(references,'MAX_BYTES',10)
    calls=[]
    class Response:
        status=403 if problem=='denied' else 200
        def getheader(self,name,default=None):return 'text/html' if name=='Content-Type' else default
        def read1(self,size):return b'X'*11
    class Connection:
        def __init__(self,*a,**k):calls.append(self)
        def request(self,*a,**k):
            if problem=='certificate':raise ssl.SSLCertVerificationError('invalid certificate')
        def getresponse(self):return Response()
        def close(self):pass
    monkeypatch.setattr(references.http.client,'HTTPSConnection',Connection)
    with pytest.raises(references.ReferenceError):references._download('https://example.org/a')
    assert len(calls)==1


def test_multiple_references_persist_separately_and_cascade():
    c = client()
    login(c)
    did = c.post("/api/drafts", json={"platform": "wechat", "title": "我的稿件", "body": "我的正文"}, headers=H).json()["id"]
    before = c.get(f"/api/drafts/{did}").json()
    for i in range(4):
        result = c.post(f"/api/drafts/{did}/references", json={"title": f"对标{i}", "body": f"原文{i}", "url": URL}, headers=H)
        assert result.status_code == 200, result.text
    items = c.get(f"/api/drafts/{did}/references").json()
    assert len(items) == 4 and len({item["id"] for item in items}) == 4
    assert c.get(f"/api/drafts/{did}").json() == before
    assert db.rows("SELECT * FROM jobs") == []
    assert c.post(f"/api/drafts/{did}/references", json={"body": ""}, headers=H).status_code == 400
    assert c.post(f"/api/drafts/{did}/references", json={"body": "原文", "url": "javascript:alert(1)"}, headers=H).status_code == 400
    c.delete(f"/api/drafts/{did}", headers=H)
    assert db.rows("SELECT * FROM draft_references") == []


def test_read_requires_auth_origin_and_existing_draft(monkeypatch):
    c = client()
    assert c.get("/api/drafts/1/references").status_code == 401
    login(c)
    assert c.post("/api/drafts/999/references/read", json={"url": URL}, headers=H).status_code == 404
    did = c.post("/api/drafts", json={"platform": "wechat"}, headers=H).json()["id"]
    assert c.post(f"/api/drafts/{did}/references/read", json={"url": URL}).status_code == 403
    monkeypatch.setattr(references, "read_article", lambda url: {"title": "原文", "body": "原文正文", "url": url})
    assert c.post(f"/api/drafts/{did}/references/read", json={"url": URL}, headers=H).json()["body"] == "原文正文"
    assert c.get(f"/api/drafts/{did}/references").json() == []  # reading is separate from saving


def test_reference_limit_does_not_discard_existing_items():
    c = client()
    login(c)
    did = c.post("/api/drafts", json={"platform": "x"}, headers=H).json()["id"]
    for _ in range(20):
        assert c.post(f"/api/drafts/{did}/references", json={"body": "原文"}, headers=H).status_code == 200
    assert c.post(f"/api/drafts/{did}/references", json={"body": "另一篇"}, headers=H).status_code == 400
    assert len(c.get(f"/api/drafts/{did}/references").json()) == 20


def test_wechat_picture_post_excludes_nested_watermarks():
    html = '''<meta property="og:title" content="图片帖"><meta property="og:description" content="正文&lt;a class='wx_topic_link'&gt;#标签&lt;/a&gt;">
    <script>var data = {picture_page_info_list: [
      {cdn_url:'https://example.org/first.png', watermark_info:{cdn_url:'https://example.org/watermark.png'}, tags:[]},
      {cdn_url:'https://example.org/second.png',share_cover:{cdn_url:'https://example.org/cover.png'}}
    ]};</script>'''
    result = references.extract_article(html, URL)
    assert result['title'] == '图片帖'
    assert 'wx_topic_link' not in result['body'] and '#标签' in result['body']
    assert result['body'].count('![原文配图]') == 2
    assert 'watermark.png' not in result['body'] and 'cover.png' not in result['body']
    assert result['body'].index('first.png') < result['body'].index('second.png')


def test_xhs_reads_only_requested_note_and_preserves_undefined_text():
    html = '''<script>window.__INITIAL_STATE__={"other":undefined,"note":{"noteDetailMap":{
    "abc":{"note":{"title":"标题","desc":"undefined 正文","imageList":[{"urlDefault":"https://example.org/a.jpg"}]}},
    "def":{"note":{"title":"别的推荐","desc":"不能混入"}}
    }}}</script>'''
    result = references.extract_article(html, 'https://www.xiaohongshu.com/explore/abc?xsec_token=example')
    assert result['title'] == '标题' and 'undefined 正文' in result['body']
    assert 'a.jpg' in result['body'] and '不能混入' not in result['body']
    with pytest.raises(references.ReferenceError, match='未读到小红书笔记'):
        references.extract_article(html, 'https://www.xiaohongshu.com/explore/missing')


def test_share_text_extracts_link_and_keeps_parameters(monkeypatch):
    calls = []
    def download(url):
        calls.append(url)
        return '<article>' + '原文正文' * 30 + '</article>', url
    monkeypatch.setattr(references, '_download', download)
    references.read_article('推荐文章 https://example.org/note?xsec_token=example 复制打开')
    assert calls == ['https://example.org/note?xsec_token=example']
    with pytest.raises(references.ReferenceError):
        references.read_article('https://example.org/a https://example.org/b')


def test_highlights_are_persistent_scoped_and_do_not_change_source():
    c = client(); login(c)
    did = c.post('/api/drafts', json={'platform':'wechat','body':'我的稿件'}, headers=H).json()['id']
    rid = c.post(f'/api/drafts/{did}/references', json={'body':'原文第一句。原文第二句。'}, headers=H).json()['id']
    before = c.get(f'/api/drafts/{did}').json()
    original = c.get(f'/api/drafts/{did}/references').json()
    path = f'/api/drafts/{did}/references/{rid}/highlights'
    payload = {'start':0,'end':6,'quote':'原文第一句。'}
    assert c.post(path,json=payload).status_code == 403
    r = c.post(path,json=payload,headers=H); assert r.status_code == 200
    hid = r.json()['id']
    assert c.post(path,json=payload,headers=H).json()['id'] == hid
    assert c.get(path).json()[0]['quote'] == payload['quote']
    assert c.get(f'/api/drafts/{did+100}/references/{rid}/highlights').status_code == 404
    assert c.post(path,json={**payload,'end':-1},headers=H).status_code == 400
    assert c.get(f'/api/drafts/{did}').json() == before
    assert c.get(f'/api/drafts/{did}/references').json() == original
    assert c.delete(f'{path}/{hid}',headers=H).status_code == 200
    assert c.get(path).json() == []
    c.post(path,json=payload,headers=H)
    c.delete(f'/api/drafts/{did}',headers=H)
    assert db.rows('SELECT * FROM reference_highlights') == []
