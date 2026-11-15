"""Deterministic account header/footer, kept separate from article Markdown.

No models or remote calls. Account text and the selected card are private settings
and private image storage, never packaged as defaults for another installation.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from . import db, images
from .config import Settings
from .textutil import wechat_html
from .wechat_layout import _Fragment


def profile(settings: Settings, theme: str) -> dict:
    value = settings.wechat_branding
    return value if theme and isinstance(value, dict) and value.get("enabled") is True else {}


def set_number(settings: Settings, draft_id: int, number: int) -> dict:
    """Change only this article's number, without touching its text or other drafts.

    The number belongs to the author. Two drafts may carry the same one (an
    article reissued by hand, a draft of something already published); nothing is
    overwritten, we only report who else shows the same number.
    """
    if type(number) is not int or not 1 <= number <= 1000000:
        raise ValueError("请输入 1～1000000 之间的原创编号")
    if settings.wechat_branding.get("enabled") is not True:
        raise ValueError("尚未配置固定首尾排版")
    with db.tx() as conn:
        draft = conn.execute("SELECT platform FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if not draft or draft["platform"] != "wechat":
            raise ValueError("原创编号只适用于已有的公众号稿件")
        shared = conn.execute(
            "SELECT n.draft_id, d.title FROM wechat_brand_numbers n JOIN drafts d ON d.id=n.draft_id "
            "WHERE n.number=? AND n.draft_id<>? ORDER BY n.draft_id", (number, draft_id)).fetchall()
        conn.execute("INSERT INTO wechat_brand_numbers(draft_id,number) VALUES (?,?) "
                     "ON CONFLICT(draft_id) DO UPDATE SET number=excluded.number", (draft_id, number))
        conn.execute("INSERT INTO wechat_brand_sequence(id,next_number) VALUES (1,?) "
                     "ON CONFLICT(id) DO UPDATE SET next_number=MAX(next_number,excluded.next_number)", (number + 1,))
    return {"number": number, "shared_with": [{"draft_id": row["draft_id"], "title": row["title"] or ""} for row in shared]}


def number_for(settings: Settings, draft_id: int | None) -> int | None:
    """Reserve once per saved article; preview/retry/theme switches reuse it.

    A reservation is a layout number, not evidence that WeChat published anything.
    The monotonic counter survives deleting or abandoning a numbered draft.
    Anonymous scratch previews never reserve a number.
    """
    if draft_id is None:
        return None
    with db.tx() as conn:
        found = conn.execute("SELECT number FROM wechat_brand_numbers WHERE draft_id=?", (draft_id,)).fetchone()
        if found:
            return found["number"]
        start = settings.wechat_branding.get("first_number")
        sequence = conn.execute("SELECT next_number FROM wechat_brand_sequence WHERE id=1").fetchone()
        if (type(start) is not int or start < 1) and not sequence:
            return None
        start = start if type(start) is int and start > 0 else 1
        draft = conn.execute("SELECT platform FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if not draft or draft["platform"] != "wechat":
            return None
        number = max(start, sequence["next_number"] if sequence else start)
        conn.execute("INSERT INTO wechat_brand_numbers(draft_id,number) VALUES (?,?)", (draft_id, number))
        conn.execute("INSERT INTO wechat_brand_sequence(id,next_number) VALUES (1,?) "
                     "ON CONFLICT(id) DO UPDATE SET next_number=excluded.next_number", (number + 1,))
        return number


def render(settings: Settings, markdown: str, theme: str, *, title: str = "",
           draft_id: int | None = None) -> tuple[str, dict | None]:
    config = profile(settings, theme)
    if not config:
        return wechat_html(markdown, theme, title=title), None
    card = images.get(config.get("card_image_id"))
    if not card or card["draft_id"] is not None or not images.path_of(card).is_file():
        raise images.ImageError("固定名片图片找不到了，请恢复名片配置后再排版")
    # Only exact, explicitly configured old suffixes are hidden at render time.
    # Never rewrite stored articles or broadly remove a paragraph mentioning the IP.
    source = markdown.rstrip()
    for suffix in config.get("legacy_footer_suffixes", []):
        if isinstance(suffix, str) and suffix.strip() and source.endswith(suffix.rstrip()):
            source = source[:-len(suffix.rstrip())].rstrip()
            break
    number = number_for(settings, draft_id)
    fragment = _Fragment(wechat_html(source, theme, title=title)).root
    root = fragment[0]
    name = str(config.get("name") or "")
    opening = ET.Element("p", {"data-workbench-brand": "opening", "style":
        "margin:0 0 28px;text-align:center;font-size:12px;line-height:1.85;color:#7c858d;letter-spacing:0"})
    opening.text = f"这是「{name}」的第 {number if number is not None else 'N'} 篇 AI 原创。"
    heading = next((i for i, node in enumerate(root) if node.tag == "h1"), -1)
    root.insert(heading + 1, opening)
    footer = ET.SubElement(root, "section", {"data-workbench-brand": "footer", "style": "margin:36px 0 0;padding:0"})
    cta = ET.SubElement(footer, "p", {"data-workbench-brand": "cta", "style":
        "margin:0 0 24px;padding:22px 0 0;border-top:1px solid #d8f4df;text-align:center;"
        "font-size:13px;font-weight:600;line-height:1.9;color:#183247;letter-spacing:0"})
    cta.text = str(config.get("cta") or "")
    ET.SubElement(footer, "img", {"src": f'/api/images/{card["id"]}', "alt": name + "名片",
        "style": "display:block;width:100%;max-width:100%;height:auto;margin:0 0 26px;border-radius:12px"})
    for text in ("点赞", "在看", "转发"):
        action = ET.SubElement(footer, "p", {"data-workbench-brand": "action", "style":
            "margin:0 0 12px;text-align:center;font-size:12px;line-height:1.9;color:#1677ff;letter-spacing:0"})
        action.text = text
    end = ET.SubElement(footer, "p", {"data-workbench-brand": "end", "style":
        "margin:25px 0 0;text-align:center;font-size:11px;line-height:1.8;color:#87989b;letter-spacing:3px"})
    end.text = "END"
    output = "".join(ET.tostring(node, encoding="unicode", method="html") for node in fragment)
    return output, {"enabled": True, "number": number, "needs_start_number": number is None}
