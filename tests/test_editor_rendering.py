"""Regression examples for the editor's stored format and WeChat export."""
import html

import pytest

from content_workbench.textutil import normalize_emphasis, wechat_html
from content_workbench.wechat_layout import THEME_IDS, _Fragment


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
