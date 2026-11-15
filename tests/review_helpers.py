from test_app import ORIGIN


def reviewed(c,did):
    for stage in ('body','layout'):
        v=c.get(f'/api/drafts/{did}/workflow').json()
        response=c.post(f'/api/drafts/{did}/approve',json={'stage':stage,'confirm':True,'hash':v[stage+'_hash']},headers={'Origin':ORIGIN})
        assert response.status_code==200,response.text
