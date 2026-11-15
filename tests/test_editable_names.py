from content_workbench import db
from content_workbench.config import Settings
from test_app import client,login,ORIGIN
from test_editor_browser import editor_server
from playwright.sync_api import sync_playwright,expect

H={'Origin':ORIGIN}


def test_workbench_name_persists_without_changing_product_or_model():
    c=client(workspace_label="产品范围",llm_backend="disabled")
    login(c)
    assert c.put('/api/settings',headers=H,json={'workbench_name':'  我的工作台  '}).status_code==200
    assert c.get('/api/settings').json()['workbench_name']=='我的工作台'
    assert c.get('/api/status').json()['workbench_name']=='我的工作台'
    assert Settings.load().workbench_name=='我的工作台'
    assert Settings.load().workspace_label=='产品范围'
    assert Settings.load().llm_backend=='disabled'
    for value in ('', '  ', 'x'*41, '分\n行', 123):
        assert c.put('/api/settings',headers=H,json={'workbench_name':value}).status_code==400
    assert Settings.load().workbench_name=='我的工作台'


def test_idea_title_validation_and_snapshot_uses_saved_title():
    c=client();login(c)
    iid=c.post('/api/ideas',headers=H,json={'title':'旧标题'}).json()['id']
    for title in ('', ' ', 'x'*301, None):
        assert c.patch(f'/api/ideas/{iid}',headers=H,json={'title':title}).status_code==400
    assert c.patch(f'/api/ideas/{iid}',headers=H,json={'title':'  新标题  '}).status_code==200
    did=c.post(f'/api/ideas/{iid}/draft',headers=H,json={'platform':'wechat','preparation':{}}).json()['id']
    draft=c.get(f'/api/drafts/{did}').json()
    assert draft['title']=='新标题' and draft['writing_context']['title']=='新标题'
    assert not db.rows("SELECT * FROM jobs WHERE state='running'")


def test_edit_title_in_confirmation_and_click_workspace_name(editor_server):
    base,c=editor_server
    iid=c.post('/api/ideas',json={'title':'弹窗原选题','angle':'保留原角度'}).json()['id']
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chromium')
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.goto(base+'/#/ideas')
            page.get_by_role('button',name='修改工作台名称',exact=True).click()
            name_dialog=page.get_by_role('dialog')
            name_dialog.get_by_label('工作台名称',exact=True).fill('测试的内容工作台')
            name_dialog.get_by_role('button',name='保存名称',exact=True).click()
            expect(page.get_by_role('button',name='修改工作台名称',exact=True)).to_have_text('测试的内容工作台')
            page.reload()
            expect(page.get_by_role('button',name='修改工作台名称',exact=True)).to_have_text('测试的内容工作台')
            expect(page).to_have_title('测试的内容工作台')
            page.get_by_role('button',name='写成公众号',exact=True).click()
            dialog=page.get_by_role('dialog')
            title=dialog.get_by_label('选题标题',exact=True)
            expect(title).to_have_value('弹窗原选题')
            title.fill('弹窗里保存的新选题')
            dialog.get_by_role('button',name='保存标题',exact=True).click()
            expect(dialog.get_by_text('标题已保存',exact=True)).to_be_visible()
            dialog.get_by_role('button',name='返回补材料',exact=True).click()
            expect(page.locator('.idea h3')).to_contain_text('弹窗里保存的新选题')
            page.get_by_role('button',name='写成公众号',exact=True).click()
            dialog=page.get_by_role('dialog')
            dialog.get_by_label('选题标题',exact=True).fill('无需另点保存也带入起稿的新选题')
            dialog.get_by_role('checkbox',name='我已核对方向和材料缺口').check()
            dialog.get_by_role('button',name='确认方向，写初稿',exact=True).click()
            page.wait_for_url('**/#/drafts/*')
            draft=c.get('/api/drafts').json()[0]
            data=c.get(f"/api/drafts/{draft['id']}").json()
            assert data['title']=='无需另点保存也带入起稿的新选题'
            assert data['writing_context']['title']==data['title']
            assert db.row('SELECT angle FROM ideas WHERE id=?',(iid,))['angle']=='保留原角度'
        finally:browser.close()
