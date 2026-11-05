from content_workbench import db
from test_app import client, login, ORIGIN
H={'Origin':ORIGIN}


def test_editor_conflict_preserves_server_body_and_review_marks_are_internal():
    c=client();login(c)
    did=c.post('/api/drafts',headers=H,json={'platform':'wechat','title':'保护稿件','body':'需要核实的句子。'}).json()['id']
    initial=c.get(f'/api/drafts/{did}').json()
    marks=[{'id':1,'quote':'需要核实','prefix':'','suffix':'的句子。'}]
    first=c.put(f'/api/drafts/{did}',headers=H,json={'body':'服务器新稿。','review_issues':marks,'expected_updated_at':initial['updated_at']})
    assert first.status_code==200
    result=c.get(f'/api/drafts/{did}').json()
    assert result['review_issues']==marks and result['updated_at']==first.json()['updated_at']
    assert c.put(f'/api/drafts/{did}',headers=H,json={'body':'过时的本机稿。','expected_updated_at':initial['updated_at']}).status_code==409
    assert c.get(f'/api/drafts/{did}').json()['body']=='服务器新稿。'
    preview=c.post('/api/preview',headers=H,json={'platform':'wechat','body':result['body'],'title':result['title']}).json()
    assert 'review-issue' not in preview['html']
    assert '需要核实' not in preview['html']


def test_invalid_red_marks_cannot_overwrite_content():
    c=client();login(c)
    did=c.post('/api/drafts',headers=H,json={'platform':'wechat','body':'原稿'}).json()['id']
    assert c.put(f'/api/drafts/{did}',headers=H,json={'body':'错误稿','review_issues':[{'id':False,'quote':'x'}]}).status_code==400
    assert db.row('SELECT body FROM drafts WHERE id=?',(did,))['body']=='原稿'
