import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from content_workbench import db, images, xhs, xhs_browser as web
from test_app import client, login, ORIGIN


def sample():
    c = client()
    login(c)
    did = c.post('/api/drafts', json={'platform': 'wechat'}, headers={'Origin': ORIGIN}).json()['id']
    out = io.BytesIO()
    Image.new('RGB', (8, 8), 'white').save(out, format='PNG')
    image = images.save(did, 'sample.png', out.getvalue())
    xhs.save(did, {'title': '预约先核对', 'body': '先看时间和参考图。', 'tags': ['美甲'], 'extra_images': [image['id']]})
    return c, did, image


def test_payload_checks_images_and_combined_text_before_upload():
    c, did, image = sample()
    d = db.row('SELECT * FROM drafts WHERE id=?', (did,))
    item = web.payload(d)
    assert item['body'] == '先看时间和参考图。\n\n#美甲'
    assert len(item['files']) == 1
    s = xhs.load(d)
    s['body'] = '字' * 999
    d['xhs'] = json.dumps(s)
    with pytest.raises(web.BridgeError, match='合计'):
        web.payload(d)
    s['body'] = '正文'
    s['extra_images'] = [image['id'], image['id']]
    d['xhs'] = json.dumps(s)
    with pytest.raises(web.BridgeError, match='不重复'):
        web.payload(d)
    s['extra_images'] = [image['id']]
    d.update(id=did + 1, xhs=json.dumps(s))
    with pytest.raises(web.BridgeError, match='不属于'):
        web.payload(d)


def test_api_requires_confirmation_and_connection_without_launching_browser():
    c, did, _ = sample()
    headers = {'Origin': ORIGIN}
    url = f'/api/drafts/{did}/xhs/save-web-draft'
    assert c.post(url, json={}, headers=headers).status_code == 400
    response = c.post(url, json={'confirm': True}, headers=headers)
    assert response.status_code == 400 and '先连接' in response.json()['detail']
    assert c.get('/api/xhs/browser').json()['state'] == 'disconnected'
    assert not db.rows('SELECT * FROM publications')


class Node:
    def __init__(self, page, kind, value=''):
        self.page, self.kind, self.value = page, kind, value

    def all(self): return [self]
    def count(self): return 1
    def is_visible(self): return True
    def is_enabled(self): return True
    def input_value(self): return self.value
    def inner_text(self): return self.value
    def get_attribute(self, name): return (self.page.accept if self.page.image_mode else 'video/mp4') if name == 'accept' else None
    def locator(self, selector): return self
    def fill(self, value): self.value = value
    def set_input_files(self, paths): self.page.files = paths
    def click(self, **kwargs):
        self.page.clicks.append(self.kind)
        if self.kind == 'draft': self.page.clicked = True
        if self.kind == 'image-tab': self.page.image_mode = True


class Empty:
    def all(self): return []
    def count(self): return 0


class CreatorFixture:
    """Offline DOM contract; intentionally no real account or browser is used."""
    url = web.CREATOR

    def __init__(self, success=True, save_label='暂存离开'):
        self.success, self.save_label = success, save_label
        self.clicked, self.clicks, self.files = False, [], []
        self.accept = 'image/jpeg,image/png'
        self.image_mode = False
        self.title = Node(self, 'title')
        self.body = Node(self, 'body')
        self.context = SimpleNamespace(new_page=lambda: self, pages=[])

    def goto(self, url, **kwargs):
        self.navigated_url = url
    def bring_to_front(self): pass
    def locator(self, selector):
        if selector == web.TITLE_SELECTORS[0]: return self.title
        if selector == web.BODY_SELECTORS[0]: return self.body
        if selector == 'input[type="file"]': return Node(self, 'upload')
        if selector == '.img-preview-area .pr': return Node(self, 'preview')
        return Empty()

    def get_by_text(self, text, **kwargs):
        if text == '上传图文': return Node(self, 'image-tab', '上传图文')
        if text is web.SAVED_MESSAGE:
            return Node(self, 'receipt') if self.clicked and self.success else Empty()
        return Empty()

    def get_by_role(self, role, name):
        # A publish-only button must never qualify as a draft action.
        return Node(self, 'draft') if name.fullmatch(self.save_label) else Empty()

    def wait_for_timeout(self, milliseconds):
        if not self.clicked:
            return
        raise TimeoutError('fixture has no further response')


def item():
    return dict(draft_id=9, title='预约先核对', body='先看时间和参考图。', files=['fixture.png'], fingerprint='test-fingerprint')


def test_only_draft_action_can_be_clicked_and_visible_receipt_is_required():
    bridge = web.Bridge()
    page = CreatorFixture()
    bridge._deliver(page, item())
    assert page.clicks == ['image-tab', 'draft']
    assert bridge.status(9)['delivery']['state'] == 'saved'
    assert page.title.value == item()['title'] and page.body.value == item()['body']


def test_publish_button_is_never_used_as_fallback():
    bridge = web.Bridge()
    page = CreatorFixture(save_label='发布')
    bridge._deliver(page, item())
    assert page.clicks == ['image-tab']
    assert bridge.status(9)['delivery']['state'] == 'unknown'


def test_missing_receipt_blocks_duplicate_even_after_restart():
    bridge = web.Bridge()
    page = CreatorFixture(success=False)
    bridge._deliver(page, item())
    assert bridge.status(9)['delivery']['state'] == 'unknown'
    restarted = web.Bridge()
    with pytest.raises(web.BridgeError, match='核对'):
        restarted.submit(item())
    assert page.clicks == ['image-tab', 'draft']


def test_saved_identical_content_is_idempotent():
    bridge = web.Bridge()
    bridge._record(item(), 'saved', '保存成功')
    assert bridge.submit(item())['delivery']['state'] == 'saved'
    with pytest.raises(web.BridgeError, match='修改版'):
        bridge.submit({**item(), 'fingerprint': 'changed'})


def test_interrupted_save_does_not_become_automatic_retry():
    bridge = web.Bridge()
    bridge._record(item(), 'saving', '存稿中')
    assert web.Bridge().status(9)['delivery']['state'] == 'unknown'


def test_manual_reconciliation_never_resends_anything():
    bridge = web.Bridge()
    bridge._record(item(), 'unknown', '结果待核对')
    assert bridge.reconcile(9, 'not_saved')['delivery']['state'] == 'failed'
    assert bridge.commands.empty()
    bridge._record(item(), 'unknown', '结果待核对')
    assert bridge.reconcile(9, 'saved')['delivery']['state'] == 'saved'
    assert bridge.commands.empty()
    with pytest.raises(web.BridgeError):
        bridge.reconcile(9, 'not_saved')


def test_existing_editor_text_is_not_overwritten():
    bridge = web.Bridge()
    page = CreatorFixture()
    page.title.value = '用户未完成的稿件'
    bridge._deliver(page, item())
    assert page.title.value == '用户未完成的稿件' and not page.files
    assert bridge.status(9)['delivery']['state'] == 'failed'


def test_upload_waits_for_spa_and_handles_duplicate_text_and_extension_accept():
    page = CreatorFixture()
    page.accept = '.jpg,.jpeg,.png,.webp'
    waits = []

    class Nodes:
        def __init__(self, nodes): self.nodes = nodes
        def all(self): return self.nodes

    hidden = Node(page, 'hidden-tab', '上传图文')
    hidden.is_visible = lambda: False
    tab = Node(page, 'image-tab', '上传图文')
    original_locator = page.locator

    def locator(selector):
        if selector == 'div.creator-tab':
            return Empty() if len(waits) < 2 else Nodes([hidden, tab])
        return original_locator(selector)

    original_text = page.get_by_text
    page.get_by_text = lambda text, **kw: Empty() if text == '上传图文' else original_text(text, **kw)
    page.locator = locator
    page.wait_for_timeout = lambda milliseconds: waits.append(milliseconds)
    upload = web.wait_image_upload(page)
    assert upload.kind == 'upload'
    assert waits[:2] == [300, 300] and 'hidden-tab' not in page.clicks
    assert page.clicks == ['image-tab']


def test_video_only_input_is_never_selected(monkeypatch):
    page = CreatorFixture()
    page.accept = 'video/mp4,.mov'
    now = [0.0]
    monkeypatch.setattr(web.time, 'monotonic', lambda: now[0])
    page.wait_for_timeout = lambda ms: now.__setitem__(0, now[0] + ms / 1000)
    with pytest.raises(web.BridgeError, match='未能就绪'):
        web.wait_image_upload(page, timeout=1)
    assert page.files == []


def test_navigation_error_reports_stage_without_leaking_browser_details():
    bridge = web.Bridge()
    page = CreatorFixture()
    def fail(*args, **kwargs):
        raise TimeoutError('private session data must not appear')
    page.goto = fail
    bridge._deliver(page, item())
    result = bridge.status(9)['delivery']
    assert result['state'] == 'failed'
    assert result['message'] == '加载小红书创作页：等待超时；未确认保存成功'
    assert 'private' not in result['message']
    assert not page.files and not page.clicks


def test_temporarily_blocked_image_tab_retries_after_escape():
    page = CreatorFixture()
    tab = Node(page, 'image-tab', '上传图文')
    attempts = []
    keys = []
    page.keyboard = SimpleNamespace(press=keys.append)
    def click(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise TimeoutError('overlay intercepts click')
        page.clicks.append('image-tab')
        page.image_mode = True
    tab.click = click
    original = page.get_by_text
    page.get_by_text = lambda text, **kw: tab if text == '上传图文' else original(text, **kw)
    assert web.wait_image_upload(page).kind == 'upload'
    assert keys == ['Escape']
    assert len(attempts) == 2 and attempts[0]['timeout'] == 1500
    assert page.clicks == ['image-tab']


def test_visible_image_button_uses_file_chooser_without_reclicking_tab():
    page = CreatorFixture()
    button = Node(page, 'upload-image')
    original = page.get_by_text
    page.get_by_text = lambda text, **kw: button if text == '上传图片' else original(text, **kw)
    events = []
    chooser = SimpleNamespace(is_multiple=lambda: True, set_files=lambda files: events.append(files))
    class Pending:
        value = chooser
        def __enter__(self):
            events.append('listening')
            return self
        def __exit__(self, *args): pass
    page.expect_file_chooser = lambda **kw: Pending()
    upload = web.wait_image_upload(page)
    upload.set_input_files(['first.png', 'second.png'])
    assert events == ['listening', ['first.png', 'second.png']]
    assert page.clicks == ['upload-image']


def test_image_button_does_not_upload_after_leaving_official_host():
    page = CreatorFixture()
    upload = web.ImageButtonUpload(page, Node(page, 'upload-image'))
    page.url = 'https://example.com/'
    with pytest.raises(web.BridgeError, match='已离开'):
        upload.set_input_files(['first.png'])
    assert not page.clicks and not page.files


def test_image_tab_click_without_transition_is_retried(monkeypatch):
    page = CreatorFixture()
    now = [0.0]
    monkeypatch.setattr(web.time, 'monotonic', lambda: now[0])
    page.wait_for_timeout = lambda ms: now.__setitem__(0, now[0] + ms / 1000)
    original = page.locator
    def locator(selector):
        if selector == 'input[type="file"]' and page.clicks.count('image-tab') < 2:
            return Empty()
        return original(selector)
    page.locator = locator
    assert web.wait_image_upload(page).kind == 'upload'
    assert page.clicks == ['image-tab', 'image-tab']
    assert now[0] >= 1.5


def test_tab_click_alone_never_confirms_image_upload(monkeypatch):
    page = CreatorFixture()
    page.accept = 'video/mp4'
    now = [0.0]
    monkeypatch.setattr(web.time, 'monotonic', lambda: now[0])
    page.wait_for_timeout = lambda ms: now.__setitem__(0, now[0] + ms / 1000)
    with pytest.raises(web.BridgeError, match='未能就绪'):
        web.wait_image_upload(page, timeout=4)
    assert page.clicks.count('image-tab') >= 2
    assert not page.files


def empty_image_page():
    page = CreatorFixture()
    page.is_closed = lambda: False
    original = page.get_by_text
    page.get_by_text = lambda text, **kw: Node(page, 'image-upload') if text == '上传图片' else original(text, **kw)
    page.locator = lambda selector: Empty()
    return page


def test_reuses_only_empty_image_page_and_leaves_existing_editor_alone():
    empty = empty_image_page()
    editor = empty_image_page()
    editor.locator = lambda selector: Node(editor, 'title', 'existing content') if selector == web.TITLE_SELECTORS[0] else Empty()
    context = SimpleNamespace(pages=[editor, empty])
    assert web.existing_empty_image_page(context) is empty
    assert editor.clicks == []


def test_ambiguous_empty_image_pages_do_not_receive_wrong_draft():
    with pytest.raises(web.BridgeError, match='多个'):
        web.existing_empty_image_page(SimpleNamespace(pages=[empty_image_page(), empty_image_page()]))


def test_each_draft_keeps_its_own_delivery_record():
    bridge = web.Bridge()
    bridge._record(item(), 'saved', 'first saved')
    second = dict(item(), draft_id=10, title='second', fingerprint='second')
    bridge._record(second, 'failed', 'second failed')
    assert bridge.status(9)['delivery']['state'] == 'saved'
    assert bridge.status(10)['delivery']['state'] == 'failed'
    assert bridge.status(10)['delivery']['title'] == 'second'


def test_direct_image_route_accepts_input_without_clicking_any_tab():
    from urllib.parse import urlparse, parse_qs
    assert parse_qs(urlparse(web.CREATOR).query)['target'] == ['image']
    page = CreatorFixture()
    page.image_mode = True
    page.get_by_text = lambda *a, **kw: Empty()
    assert web.wait_image_upload(page).kind == 'upload'
    assert page.clicks == []


def test_two_distinct_deliveries_open_image_route_and_keep_separate_content():
    bridge = web.Bridge()
    first = CreatorFixture()
    second = CreatorFixture()
    second_item = dict(item(), draft_id=10, title='第二篇', body='第二篇正文', files=['second.png'], fingerprint='second')
    bridge._deliver(first, item())
    bridge._deliver(second, second_item)
    assert first.navigated_url == second.navigated_url == web.CREATOR
    assert first.title.value == item()['title']
    assert second.title.value == second_item['title'] and second.body.value == second_item['body']
    assert first.files == ['fixture.png'] and second.files == ['second.png']
    assert bridge.status(9)['delivery']['state'] == bridge.status(10)['delivery']['state'] == 'saved'


def test_upload_timeout_requires_reconciliation_instead_of_duplicate_upload():
    bridge = web.Bridge()
    page = CreatorFixture()
    node = Node(page, 'upload')
    def fail(paths): raise TimeoutError('upload may have started')
    node.set_input_files = fail
    original = page.locator
    page.locator = lambda selector: node if selector == 'input[type="file"]' else original(selector)
    bridge._deliver(page, item())
    assert bridge.status(9)['delivery']['state'] == 'unknown'
    with pytest.raises(web.BridgeError, match='核对'):
        bridge.submit(item())
