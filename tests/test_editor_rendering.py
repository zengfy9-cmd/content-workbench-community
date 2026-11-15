"""Regression examples for the editor's stored format and WeChat export."""
import html

import pytest

from content_workbench.textutil import normalize_emphasis, wechat_html
from content_workbench.wechat_layout import THEME_IDS, _Fragment


@pytest.mark.parametrize('theme', ('', *THEME_IDS))
def test_bold_with_edge_spaces_renders_without_visible_delimiters(theme):
    body = '**选题 **：说明文字。\n\n** 前后空格 **\n\n== 高亮 ==\n\n\\*\\*字面星号 \\*\\*'
    output = wechat_html(body, theme)
    root = _Fragment(output).root
    bold = [''.join(node.itertext()) for node in root.iter('strong')]
    assert '选题' in bold and '前后空格' in bold
    assert '**选题 **' not in output
    assert '**字面星号 **' in ''.join(root.itertext())
    assert '高亮' in ''.join(root.itertext())
    normalized = normalize_emphasis(body)
    assert '**选题** ：说明文字。' in normalized
    assert normalize_emphasis(normalized) == normalized


def test_prompt_and_inline_code_with_bold_examples_remain_literal():
    body = '```prompt\n**选题 **\n```\n\n`**选题 **`\n'
    assert normalize_emphasis(body) == body
    root = _Fragment(wechat_html(body)).root
    assert not list(root.iter('strong'))
    assert ''.join(next(root.iter('pre')).itertext()) == '**选题 **\n'


@pytest.mark.parametrize('theme', ('', *THEME_IDS))
def test_manual_alignment_survives_all_themes(theme):
    output = wechat_html('正文。\n\n:::center\n我是测试署名\n\n**测试结尾**\n:::\n\n:::left\n明确左对齐。\n:::\n', theme)
    nodes = list(_Fragment(output).root.iter())
    for text, align in [('我是测试署名', 'center'), ('明确左对齐。', 'left')]:
        node = next(n for n in nodes if n.text == text)
        assert f'text-align:{align}' in node.get('style', '')
        assert 'text-align:justify' not in node.get('style', '')
    assert ':::center' not in output


@pytest.mark.parametrize('theme', ('', *THEME_IDS))
def test_prompt_is_literal_multiline_text_and_export_contains_no_buttons(theme):
    text = '# 原样标题\n:::center\nC:\\资料\\文件.txt\n```python\nprint("<b>**不改写**</b>")\n```\n:::'
    output = wechat_html('````prompt\n' + text + '\n````\n', theme)
    pre = next(_Fragment(output).root.iter('pre'))
    assert pre.get('data-prompt') == 'true'
    assert ''.join(pre.itertext()) == text + '\n'
    assert '<button' not in output and '<script' not in output
    assert 'white-space:pre-wrap' in pre.get('style', '')
    assert normalize_emphasis('````prompt\n' + text + '\n````\n') == '````prompt\n' + text + '\n````\n'


def test_legacy_empty_hardbreaks_do_not_display_slashes_or_damage_paths():
    output = wechat_html('正文。\\\n\\\n\n下一段。\n\n\\\n\n`C:\\资料\\`\n\nC:\\\\资料\\\\\n')
    plain = html.unescape(''.join(_Fragment(output).root.itertext()))
    assert '正文。' in plain and '下一段。' in plain
    assert '\\' not in plain.split('下一段。')[0]
    assert plain.count('C:\\资料\\') == 2


def test_legacy_single_terminal_break_and_real_terminal_backslashes():
    output = wechat_html('句末旧标记。\\\n\n另一段。\n\nC:\\原始路径\\\n\n字面反斜杠\\\\\n\n`代码\\`\n')
    plain = ''.join(_Fragment(output).root.itertext())
    assert '句末旧标记。\\' not in plain
    assert 'C:\\原始路径\\' in plain
    assert '字面反斜杠\\' in plain
    assert '代码\\' in plain

@pytest.mark.parametrize('theme', ('', *THEME_IDS))
def test_small_blue_footer_does_not_change_regular_paragraph(theme):
    output = wechat_html('正文。\n\n:::center small-blue\n点赞\n:::\n', theme)
    nodes = list(_Fragment(output).root.iter())
    small = next(n for n in nodes if n.text == '点赞')
    plain = next(n for n in nodes if n.text == '正文。')
    style = small.get('style', '')
    assert 'font-size:12px' in style and 'color:#1677ff' in style and 'text-align:center' in style
    assert '#1677ff' not in plain.get('style', '')
    assert ':::center' not in output
