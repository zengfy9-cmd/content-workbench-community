"""Local inline renderer with community-owned minimal color themes."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from css_inline import CSSInliner

DEFAULT_THEME = "green-white-clean"
THEME_IDS = (
    DEFAULT_THEME, "ink-blue-editorial", "graphite-dossier",
    "sand-gold-journal", "mist-purple-story",
)
_ASSETS = Path(__file__).with_name("layout_themes")
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
# The upstream clipboard whitelist deliberately excludes viewport sizes/scrolling.
_SAFE_PROPS = frozenset((
    "display box-sizing margin margin-top margin-right margin-bottom margin-left "
    "padding padding-top padding-right padding-bottom padding-left color background "
    "background-color background-image background-size background-position background-repeat "
    "border border-top border-right border-bottom border-left border-radius box-shadow "
    "font-family font-size font-weight font-style font-variant-numeric line-height "
    "letter-spacing text-align text-decoration text-indent white-space word-break "
    "overflow-wrap list-style-type list-style-position vertical-align border-collapse "
    "border-spacing table-layout opacity"
).split())


@lru_cache(maxsize=5)
def _theme(theme_id: str) -> dict:
    if theme_id not in THEME_IDS:
        raise ValueError("请选择有效的 通用排版主题")
    return json.loads((_ASSETS / f"{theme_id}.json").read_text(encoding="utf-8"))


def validate_theme(value: object) -> str:
    if not isinstance(value, str) or (value and value not in THEME_IDS):
        raise ValueError("请选择有效的 通用排版主题")
    return value


def themes() -> list[dict]:
    return [{"id": key, "name": _theme(key)["name"], "accent": _theme(key)["accent"],
             "description": _theme(key)["description"]} for key in THEME_IDS]


class _Fragment(HTMLParser):
    """Parse only HTML already produced by our safe Markdown renderer."""

    def __init__(self, content: str):
        super().__init__(convert_charrefs=True)
        self.root = ET.Element("fragment")
        self.stack = [self.root]
        self.feed(content)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = ET.SubElement(self.stack[-1], tag, {key: value or "" for key, value in attrs})
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        parent = self.stack[-1]
        if len(parent):
            parent[-1].tail = (parent[-1].tail or "") + data
        else:
            parent.text = (parent.text or "") + data


def _serialize(node: ET.Element) -> str:
    # HTML5 serialization avoids namespace prefixes and keeps void elements valid.
    return ET.tostring(node, encoding="unicode", method="html")


def _split_spaced_lines(article: ET.Element) -> None:
    """The rich editor may store blank lines as two hard breaks in one <p>."""
    for paragraph in list(article):
        if paragraph.tag != "p":
            continue
        pieces = [ET.Element("p", paragraph.attrib)]
        pieces[0].text = paragraph.text
        children = list(paragraph)
        index = 0
        while index < len(children):
            child = children[index]
            end = index
            if child.tag == "br":
                while end + 1 < len(children) and not (children[end].tail or "").strip() and children[end + 1].tag == "br":
                    end += 1
            if end > index:
                block = ET.Element("p", paragraph.attrib)
                block.text = children[end].tail
                pieces.append(block)
                index = end + 1
            else:
                pieces[-1].append(deepcopy(child))
                index += 1
        if len(pieces) > 1:
            position = list(article).index(paragraph)
            article.remove(paragraph)
            for piece in pieces:
                if not "".join(piece.itertext()).strip() and not len(piece):
                    continue
                article.insert(position, piece)
                position += 1
            pieces[-1].tail = paragraph.tail


def _structure(article: ET.Element, title: str) -> None:
    """Give plain-text drafts a conservative, presentation-only heading structure.

    Explicit Markdown headings always win. Only short standalone paragraphs are
    candidates; sentences, multiline text, image notes and rich blocks stay as-is.
    Never edit or reserialize the stored Markdown.
    """
    _split_spaced_lines(article)
    has_sections = any(node.tag in {"h2", "h3", "h4", "h5", "h6"} for node in article.iter())
    if not has_sections:
        blocks = list(article)
        for index, node in enumerate(blocks[:-1]):
            if node.tag != "p" or any(child.tag != "strong" for child in node):
                continue
            text = "".join(node.itertext()).strip()
            if not 4 <= len(text) <= 32 or "\n" in text or not "".join(blocks[index + 1].itertext()).strip():
                continue
            label = re.sub(r"[（(][^（）()]*[）)]", "", text)
            if re.search(r"[。！？!?，,；;：:、…）)】]", label):
                continue
            if text.startswith(("（", "(", "【", "[")) or text.endswith(tuple("的了着过和与更在为而是到把被将")):
                continue
            # English sentences without punctuation aren't reliably distinguishable.
            if not re.search(r"[\u4e00-\u9fff]", text):
                continue
            node.tag = "h2"
            node.set("data-layout-inferred", "true")
    if title.strip() and next(article.iter("h1"), None) is None:
        heading = ET.Element("h1", {"data-layout-title": "true"})
        heading.text = title.strip()
        article.insert(0, heading)


def layout_info(output: str) -> dict:
    article = _Fragment(output).root
    return {
        "inferred_headings": sum(node.get("data-layout-inferred") == "true" for node in article.iter()),
        "has_title": next(article.iter("h1"), None) is not None,
    }


def _decorate(article: ET.Element, theme: dict) -> None:
    """Materialize the heading labels used by upstream decorateHeadings()."""
    first = next(article.iter("h1"), None)
    if first is not None and theme.get("h1_watermark"):
        original = first.text
        first.text = None
        watermark = ET.Element("span", {"class": "wechatpb-h1-watermark", "aria-hidden": "true"})
        watermark.text = str(theme["h1_watermark"])
        kicker = ET.Element("span", {"class": "wechatpb-h1-kicker", "aria-hidden": "true"})
        kicker.text = theme.get("h1_kicker", "")
        kicker.tail = original
        first.insert(0, watermark)
        first.insert(1, kicker)
    if theme.get("heading_label"):
        for index, heading in enumerate(article.iter("h2"), 1):
            label = ET.Element("span", {"class": "wechatpb-heading-label", "aria-hidden": "true"})
            label.text = f"{theme['heading_label']} {index:02d}"
            label.tail, heading.text = heading.text, None
            heading.insert(0, label)
    # Upstream wraps code fences so the original code-card CSS can take effect.
    for parent in list(article.iter()):
        for index, child in enumerate(list(parent)):
            if child.tag == "pre":
                shell = ET.Element("section", {"class": "code-section"})
                shell.tail, child.tail = child.tail, None
                parent.remove(child)
                shell.append(child)
                parent.insert(index, shell)


def _wechat_lists(article: ET.Element, accent: str) -> None:
    """Materialize one marker per item; WeChat may rewrite semantic lists."""
    for listing in reversed(list(article.iter())):
        if listing.tag not in ("ol", "ul"):
            continue
        ordered = listing.tag == "ol"
        start = int(listing.get("start", "1")) if listing.get("start", "1").isdigit() else 1
        listing.tag = "section"
        listing.attrib.pop("start", None)
        listing.set("style", listing.get("style", "") + ";padding-left:0;list-style:none;")
        for index, item in enumerate(list(listing)):
            if item.tag != "li":
                continue
            item.tag = "section"
            item.set("style", item.get("style", "") + ";display:block;")
            line = ET.Element("p", {"style": "margin:0;line-height:inherit;color:inherit;"})
            marker = ET.SubElement(line, "span", {"style": f"display:inline-block;color:{accent};margin-right:6px;"})
            marker.text = f"{start + index}." if ordered else "•"
            content = ET.SubElement(line, "span", {"style": "display:inline;"})
            content.text, item.text = item.text, None
            children = list(item)
            if children and children[0].tag == "p":
                first = children.pop(0)
                content.text = (content.text or "") + (first.text or "")
                for child in list(first):
                    content.append(child)
                item.remove(first)
            # Inline children remain on the marker's line; additional blocks keep order.
            for child in children:
                if child.tag in {"p", "section", "blockquote", "pre", "table"}:
                    break
                item.remove(child)
                content.append(child)
            item.insert(0, line)


def render(rendered_markdown: str, theme_id: str, *, title: str = "") -> str:
    theme = _theme(theme_id)
    article = _Fragment(f'<section class="note-to-mp">{rendered_markdown}</section>').root[0]
    _structure(article, title)
    for mark in article.iter("mark"):
        mark.tag = "span"
        mark.set("style", "background:#fff0a6;color:#1a1a1a;padding:0 2px;border-radius:2px;")
    _decorate(article, theme)
    # No browser, external stylesheets, user files, model calls or network requests.
    inliner = CSSInliner(load_remote_stylesheets=False, keep_style_tags=False, keep_link_tags=False)
    result = inliner.inline_fragment(_serialize(article), theme["css"])
    tree = _Fragment(result).root[0]
    for node in tree.iter():
        declarations = []
        for part in node.get("style", "").split(";"):
            key, sep, value = part.partition(":")
            if sep and (key.strip() in _SAFE_PROPS or (node.tag == "hr" and key.strip() == "height")):
                declarations.append(f"{key.strip()}:{value.strip()}")
        if declarations:
            node.set("style", ";".join(declarations) + ";")
        else:
            node.attrib.pop("style", None)
        if node.tag == "table":
            node.set("style", node.get("style", "") + "width:100%;table-layout:fixed;")
    _wechat_lists(tree, theme["accent"])
    # Keep the output fluid inside the workbench phone and in the WeChat editor.
    tree.set("style", tree.get("style", "") + ";max-width:100%;box-sizing:border-box;")
    for image in tree.iter("img"):
        image.set("style", image.get("style", "") + ";max-width:100%;height:auto;")
    return _serialize(tree)
