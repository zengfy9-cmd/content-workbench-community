"""Visible, owner-operated browser connection for Xiaohongshu web drafts.

Only exact draft-save controls are ever clicked. No publishing fallback, cookie
export, anti-bot bypass or automatic retries after a save may have occurred.
"""
from __future__ import annotations

import hashlib
import json
import queue
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import images, xhs
from .config import _write_private, data_dir

# Public creator route used by qjt0111/xiaohongshu-mcp-python (xhs_actions.py).
CREATOR = 'https://creator.xiaohongshu.com/publish/publish?source=official&from=tab_switch&target=image'
DRAFT_BUTTON = re.compile(r'^(暂存离开|存草稿|保存草稿)$')
SAVED_MESSAGE = re.compile(r'(草稿保存成功|已保存至草稿箱|已存入草稿箱|暂存成功|保存草稿成功)')
TITLE_SELECTORS = ('input[placeholder*="标题"]', 'textarea[placeholder*="标题"]')
BODY_SELECTORS = ('div[role="textbox"][contenteditable="true"]', 'div.tiptap[contenteditable="true"]', 'div.ql-editor[contenteditable="true"]')


class BridgeError(ValueError):
    pass


def payload(draft: dict) -> dict:
    state = xhs.load(draft)
    title = str(state.get('title') or '').strip()
    body = str(state.get('body') or '').strip()
    tags = [str(t).strip().lstrip('#') for t in state.get('tags') or [] if str(t).strip()]
    description = '\n\n'.join(s for s in (body, ' '.join('#' + t for t in tags)) if s)
    if not title or not body:
        raise BridgeError('先填写小红书标题和正文')
    if len(title) > 20 or len(description) > 1000:
        raise BridgeError('标题最多20字，正文与话题合计最多1000字；请先调整，不会自动截断')
    ids = list(state.get('card_images') or []) + list(state.get('extra_images') or [])
    if not 1 <= len(ids) <= 9 or len(ids) != len(set(ids)):
        raise BridgeError('请选择1～9张不重复的图片，按工作台显示顺序保存')
    files, digests = [], []
    for iid in ids:
        image = images.get(int(iid))
        if not image or image['draft_id'] != draft['id']:
            raise BridgeError('图片不属于这篇稿件')
        path = images.path_of(image).resolve()
        if path.parent != images.images_dir().resolve() or not path.is_file():
            raise BridgeError('图片文件不可用')
        files.append(str(path))
        digests.append(hashlib.sha256(path.read_bytes()).hexdigest())
    fingerprint = hashlib.sha256(json.dumps([draft['id'], title, description, digests], ensure_ascii=False).encode()).hexdigest()
    return dict(draft_id=draft['id'], title=title, body=description, files=files, fingerprint=fingerprint)


def first_visible(page, selectors):
    for selector in selectors:
        for locator in page.locator(selector).all():
            if locator.is_visible():
                return locator
    return None


def draft_button(page):
    for locator in page.get_by_role('button', name=DRAFT_BUTTON).all():
        if locator.is_visible() and locator.is_enabled():
            return locator
    # The platform sometimes renders the same exact action as a plain element.
    for locator in page.get_by_text(DRAFT_BUTTON, exact=True).all():
        if locator.is_visible() and locator.is_enabled():
            return locator
    return None


class ImageButtonUpload:
    """Use the visible image action when its file input is dynamically created."""
    def __init__(self, page, button):
        self.page, self.button = page, button

    def set_input_files(self, files):
        if urlparse(self.page.url).hostname != 'creator.xiaohongshu.com':
            raise BridgeError('页面已离开小红书官方创作页，已停止上传')
        with self.page.expect_file_chooser(timeout=10000) as pending:
            self.button.click(timeout=3000)
        chooser = pending.value
        if len(files) > 1 and not chooser.is_multiple():
            raise BridgeError('当前图片入口只允许单张上传，已停止，请在专用窗口核对')
        chooser.set_files(files)


def image_upload_control(page):
    """Find a ready image control, independent of whether a tab was clicked."""
    candidates = []
    for node in page.locator('input[type="file"]').all():
        accept = (node.get_attribute('accept') or '').lower()
        accepts_images = 'image/' in accept or any(ext in accept for ext in ('.jpg', '.jpeg', '.png', '.webp', '.heic'))
        accepts_video = 'video/' in accept or any(ext in accept for ext in ('.mp4', '.mov', '.mkv', '.webm'))
        if accepts_images and not accepts_video and node.is_enabled() and node.locator('xpath=..').is_visible():
            candidates.append(node)
    if len(candidates) == 1:
        return candidates[0]
    buttons = [node for node in page.get_by_text('上传图片', exact=True).all()
               if node.is_visible() and node.is_enabled()]
    if len(buttons) == 1:
        return ImageButtonUpload(page, buttons[0])
    return None


def wait_image_upload(page, timeout=30):
    """Open the image route first; tab clicks are only a bounded fallback."""
    deadline = time.monotonic() + timeout
    next_click = 0.0
    while time.monotonic() < deadline:
        if urlparse(page.url).hostname != 'creator.xiaohongshu.com':
            raise BridgeError('请先在专用Chrome完成小红书登录')
        upload = image_upload_control(page)
        if upload is not None:
            return upload
        if time.monotonic() >= next_click:
            tabs = page.locator('div.creator-tab').all()
            tab = next((node for node in tabs if node.is_visible() and node.inner_text().strip() == '上传图文'), None)
            if tab is None:
                tab = next((node for node in page.get_by_text('上传图文', exact=True).all() if node.is_visible()), None)
            if tab is not None:
                try:
                    tab.click(timeout=1500)
                except Exception as exc:
                    if type(exc).__name__ != 'TimeoutError':
                        raise
                    page.keyboard.press('Escape')
                next_click = time.monotonic() + 1.5
        page.wait_for_timeout(300)
    raise BridgeError('图文页未能就绪，本篇尚未上传。请保留专用Chrome页面；不要重复投递或清理浏览器数据')


def existing_empty_image_page(context):
    """Reuse only an unambiguous, empty image landing page; never an editor."""
    candidates = []
    for candidate in context.pages:
        if candidate.is_closed() or urlparse(candidate.url).hostname != 'creator.xiaohongshu.com':
            continue
        if urlparse(candidate.url).path.rstrip('/') != '/publish/publish':
            continue
        if first_visible(candidate, TITLE_SELECTORS + BODY_SELECTORS):
            continue
        if candidate.locator('.img-preview-area .pr').count():
            continue
        buttons = [node for node in candidate.get_by_text('上传图片', exact=True).all()
                   if node.is_visible() and node.is_enabled()]
        if len(buttons) == 1:
            candidates.append(candidate)
    if len(candidates) > 1:
        raise BridgeError('存在多个空白图文上传页，请只保留一个，避免传入错误标签页')
    return candidates[0] if candidates else None


class Bridge:
    def __init__(self):
        self.lock = threading.RLock()
        self.commands = queue.Queue()
        self.thread = None
        self.state = dict(state='disconnected', message='先连接小红书，在专用浏览器登录')
        self.root = data_dir() / 'xhs-browser'
        self.root.mkdir(exist_ok=True)
        self.ledger_path = self.root / 'deliveries.json'
        self.ledger = json.loads(self.ledger_path.read_text(encoding='utf-8')) if self.ledger_path.exists() else {}
        for record in self.ledger.values():
            if record['state'] in ('saving', 'uploading', 'filling'):
                record.update(state='unknown', message='上次操作被中断，请先在小红书核对，禁止自动重送')
        self._persist()

    def _persist(self):
        _write_private(self.ledger_path, json.dumps(self.ledger, ensure_ascii=False, indent=2))

    def status(self, draft_id=None):
        with self.lock:
            result = dict(self.state)
            result['bridge_revision'] = 'image-route-v3'
            if draft_id is not None:
                result['delivery'] = self.ledger.get(str(draft_id))
            return result

    def _set(self, **values):
        with self.lock:
            self.state.update(values)

    def _record(self, item, state, message):
        with self.lock:
            self.ledger[str(item['draft_id'])] = dict(state=state, message=message, title=item['title'],
                fingerprint=item['fingerprint'], updated_at=time.time())
            self._persist()
        self._set(state=state, message=message, draft_id=item['draft_id'])

    def connect(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return self.status()
            self.state = dict(state='opening', message='正在打开专用Chrome窗口，请在该窗口登录小红书')
            self.thread = threading.Thread(target=self._run, daemon=True, name='xhs-web-drafts')
            self.thread.start()
            return self.status()

    def submit(self, item):
        with self.lock:
            old = self.ledger.get(str(item['draft_id'])) or {}
            if old.get('state') in ('unknown', 'saving', 'uploading', 'filling'):
                raise BridgeError('本篇已有待核对或进行中的操作，请先在小红书检查，避免重复草稿')
            if old.get('state') == 'saved':
                if old.get('fingerprint') == item['fingerprint']:
                    return self.status(item['draft_id'])
                raise BridgeError('本篇已保存过，修改版请先在小红书原草稿中更新，避免另建重复稿')
            if not self.thread or not self.thread.is_alive() or self.state.get('state') not in ('ready', 'failed', 'saved'):
                raise BridgeError('先连接小红书并在专用Chrome窗口完成登录')
            self._record(item, 'uploading', '准备上传本篇图片')
            self.commands.put(item)
            return self.status(item['draft_id'])

    def reconcile(self, draft_id, resolution):
        with self.lock:
            record = self.ledger.get(str(draft_id))
            if not record or record['state'] != 'unknown':
                raise BridgeError('本篇没有待人工核对的存稿记录')
            if resolution not in ('saved', 'not_saved'):
                raise BridgeError('请选择已找到草稿或已确认未保存')
            record.update(state='saved' if resolution == 'saved' else 'failed', verification='manual',
                          message='你已在小红书确认找到本篇草稿' if resolution == 'saved' else '你已确认未保存；可重新连接后手动重试',
                          updated_at=time.time())
            self._persist()
            # Do not automatically navigate, upload or submit after reconciliation.
            if self.state.get('draft_id') == draft_id:
                self.state.update(state=record['state'], message=record['message'])
            return self.status(draft_id)

    def _run(self):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(str(self.root / 'profile'), channel='chrome',
                    headless=False, viewport={'width': 1280, 'height': 900}, locale='zh-CN')
                context.set_default_timeout(5000)
                page = existing_empty_image_page(context)
                already_ready = page is not None
                if page is None:
                    page = context.new_page()
                try:
                    if not already_ready:
                        page.goto(CREATOR, wait_until='domcontentloaded', timeout=30000)
                except Exception:
                    self._set(state='waiting_login', message='请在专用Chrome检查网络并完成登录；工作台继续等待')
                while context.pages:
                    if page.is_closed():
                        remaining = [candidate for candidate in context.pages if not candidate.is_closed()]
                        if not remaining:
                            break
                        page = next((candidate for candidate in remaining if urlparse(candidate.url).hostname == 'creator.xiaohongshu.com'), remaining[0])
                    if self.state['state'] in ('opening', 'waiting_login', 'ready'):
                        if urlparse(page.url).hostname == 'creator.xiaohongshu.com' and page.locator('input[type="file"]').count():
                            self._set(state='ready', message='专用浏览器已进入上传页，请确认窗口中登录的是目标账号')
                        else:
                            self._set(state='waiting_login', message='请在专用Chrome登录，并进入图文发布页；验证码或协议由你操作')
                    try:
                        item = self.commands.get_nowait()
                    except queue.Empty:
                        page.wait_for_timeout(500)
                        continue
                    self._deliver(page, item)
                context.close()
            self._set(state='disconnected', message='专用浏览器已关闭；重新连接即可继续')
        except Exception:
            # Do not expose Playwright traces, session data, page HTML or credentials.
            self._set(state='disconnected', message='专用Chrome未能运行；如有上次的专用窗口请先关闭，再重新连接。不会自动重试存稿')

    def _deliver(self, page, item):
        attempted = False
        filling = False
        uploaded = False
        step = '打开独立编辑页'
        try:
            # Keep a ready image landing page instead of resetting it to video.
            step = '查找已有空白图文页'
            ready_page = existing_empty_image_page(page.context)
            if ready_page is not None:
                page = ready_page
            else:
                page = page.context.new_page()
                step = '加载小红书创作页'
                page.goto(CREATOR, wait_until='domcontentloaded', timeout=30000)
            step = '显示创作页'
            page.bring_to_front()
            if urlparse(page.url).hostname != 'creator.xiaohongshu.com':
                raise BridgeError('当前不是小红书官方创作页，请先完成登录')
            # Leave existing typed content alone; never navigate away or overwrite it.
            title_box = first_visible(page, TITLE_SELECTORS)
            body_box = first_visible(page, BODY_SELECTORS)
            if (title_box and title_box.input_value().strip()) or (body_box and body_box.inner_text().strip()):
                raise BridgeError('创作页已有内容，请先自行处理后再送入，工作台不会覆盖')
            step = '切换上传图文并识别图片入口'
            uploads = wait_image_upload(page)
            step = '上传图片'
            # After invoking upload, a timeout can leave data in the remote editor.
            uploaded = True
            uploads.set_input_files(item['files'])
            step = '等待图片处理及编辑框'
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                title_box = first_visible(page, TITLE_SELECTORS)
                body_box = first_visible(page, BODY_SELECTORS)
                if title_box and body_box and page.locator('.img-preview-area .pr').count() == len(item['files']):
                    break
                page.wait_for_timeout(500)
            else:
                raise BridgeError('上传后未找到标题和正文，请在专用窗口检查图片上传状态')
            if title_box.input_value().strip() or body_box.inner_text().strip():
                raise BridgeError('编辑页已有文字，请人工核对，工作台没有覆盖')
            if urlparse(page.url).hostname != 'creator.xiaohongshu.com':
                raise BridgeError('页面已离开小红书官方创作页，已停止填写')
            self._record(item, 'filling', '图片已送入，正在填写标题和正文')
            filling = True
            step = '填写并核对标题正文'
            title_box.fill(item['title'])
            body_box.fill(item['body'])
            if title_box.input_value() != item['title'] or body_box.inner_text().strip() != item['body']:
                raise BridgeError('填写后回读不一致，已停止，请在专用窗口核对')
            step = '查找存草稿按钮'
            button = draft_button(page)
            if button is None:
                raise BridgeError('标题和正文已填好，但未识别到明确的存草稿按钮，请在专用窗口手动保存')
            if any(node.is_visible() for node in page.get_by_text(SAVED_MESSAGE).all()):
                raise BridgeError('页面存在旧保存回执，请先在专用窗口核对，不能据此认定本篇成功')
            if urlparse(page.url).hostname != 'creator.xiaohongshu.com':
                raise BridgeError('页面已离开小红书官方创作页，已停止存稿')
            # Persist uncertainty BEFORE clicking, so a restart can never cause a retry.
            self._record(item, 'saving', '正在点击存草稿并核对页面回执，请勿重复点击')
            attempted = True
            step = '点击存草稿并等待回执'
            button.click()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if any(node.is_visible() for node in page.get_by_text(SAVED_MESSAGE).all()):
                    self._record(item, 'saved', '小红书页面已显示草稿保存成功；请在网页草稿箱查看')
                    return
                page.wait_for_timeout(300)
            self._record(item, 'unknown', '已点击存草稿，但没有读到明确成功回执；请先在小红书核对，工作台不会自动重送')
        except Exception as exc:
            # Only expose a fixed stage and exception category, never raw browser logs.
            category = '等待超时' if type(exc).__name__ == 'TimeoutError' else '浏览器操作中断'
            message = str(exc) if isinstance(exc, BridgeError) else f'{step}：{category}；未确认保存成功'
            # Uploads and text may already be in the editor even before the save click.
            self._record(item, 'unknown' if attempted or filling or uploaded else 'failed', message)
