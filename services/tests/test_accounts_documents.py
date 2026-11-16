from io import BytesIO
import json
import time
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from services.api.accounts import Accounts, MemoryState, password_hash, password_matches
from services.api.main import create_app
from services.api.models import UserCreate, UserUpdate
from services.tests.test_services import Repo, FakeAI


@pytest.fixture
def client():
    backend=MemoryState()
    ai=FakeAI()
    with TestClient(create_app(Repo(),ai,backend)) as c:
        c.headers['X-Gaphopper-Client']='web'
        assert c.post('/api/v1/auth/login',json={'username':'gaphopper','password':'gaphopper'}).status_code==200
        yield c,ai,backend


def test_password_hashes_are_salted_and_verified():
    one,two=password_hash('test-password'),password_hash('test-password')
    assert one!=two and 'test-password' not in one
    assert password_matches('test-password',one)
    assert not password_matches('wrong',one)
    assert not password_matches('test-password','broken')


def test_login_cookie_logout_and_session_expiry(client):
    c,_,backend=client
    assert c.get('/api/v1/auth/me').json()['role']=='admin'
    assert 'gaphopper' not in c.cookies['gh_session']
    assert c.post('/api/v1/auth/logout',json={}).status_code==200
    assert c.get('/api/v1/auth/me').status_code==401
    response=c.post('/api/v1/auth/login',json={'username':'gaphopper','password':'wrong'})
    assert response.status_code==401
    response=c.post('/api/v1/auth/login',json={'username':'gaphopper','password':'gaphopper'})
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
    for session in backend.data['sessions'].values():
        session['expires']=time.time()-1
    assert c.get('/api/v1/auth/me').status_code==401


def test_users_roles_password_reset_and_disable(client):
    c,_,_=client
    assert c.post('/api/v1/admin/users',json={'username':'tester','password':'password-123'}).status_code==201
    assert c.post('/api/v1/admin/users',json={'username':'tester','password':'password-123'}).status_code==409
    response=c.get('/api/v1/admin/users')
    assert 'password' not in response.text
    assert c.patch('/api/v1/admin/users/gaphopper',json={'active':False}).status_code==409
    admin_cookie=c.cookies['gh_session']
    c.post('/api/v1/auth/login',json={'username':'tester','password':'password-123'})
    user_cookie=c.cookies['gh_session']
    assert c.get('/api/v1/jobs').status_code==200
    assert c.get('/api/v1/admin/users').status_code==403
    assert c.put('/api/v1/config',json={'provider':'gemini','model':'x'}).status_code==403
    c.cookies.set('gh_session',admin_cookie,domain='testserver.local',path='/')
    assert c.patch('/api/v1/admin/users/tester',json={'password':'new-password'}).status_code==200
    c.cookies.set('gh_session',user_cookie,domain='testserver.local',path='/')
    assert c.get('/api/v1/auth/me').status_code==401
    assert c.post('/api/v1/auth/login',json={'username':'tester','password':'password-123'}).status_code==401
    assert c.post('/api/v1/auth/login',json={'username':'tester','password':'new-password'}).status_code==200
    c.cookies.set('gh_session',admin_cookie,domain='testserver.local',path='/')
    assert c.patch('/api/v1/admin/users/tester',json={'active':False}).status_code==200
    assert c.post('/api/v1/auth/login',json={'username':'tester','password':'new-password'}).status_code==401


def test_shared_state_survives_new_app_and_model_is_used(client):
    c,ai,backend=client
    assert c.put('/api/v1/config',json={'provider':'gemini','model':'configured-model'}).status_code==200
    with TestClient(create_app(Repo(),ai,backend)) as other:
        other.headers['X-Gaphopper-Client']='web'
        other.cookies.set('gh_session',c.cookies['gh_session'])
        assert other.get('/api/v1/auth/me').status_code==200
        assert other.get('/api/v1/config').json()['default_model']=='configured-model'
        assert other.post('/api/v1/job-maps',json={'tasks':['Új feladat']}).status_code==200
        assert ai.calls[-1][0].model=='configured-model'


def test_csrf_and_bearer_role_separation(client,monkeypatch):
    c,_,_=client
    c.headers.pop('X-Gaphopper-Client')
    assert c.put('/api/v1/config',json={'provider':'openai','model':'x'}).status_code==403
    monkeypatch.setenv('SERVICE_API_TOKEN','test-api-token')
    c.cookies.clear()
    assert c.get('/api/v1/jobs',headers={'Authorization':'Bearer test-api-token'}).status_code==200
    assert c.get('/api/v1/admin/users',headers={'Authorization':'Bearer test-api-token'}).status_code==401
    assert c.get('/api/v1/jobs',headers={'Authorization':'Bearer wrong'}).status_code==401


def test_failed_login_limit_is_persistent(client):
    c,_,_=client
    c.cookies.clear()
    for _ in range(10):
        assert c.post('/api/v1/auth/login',json={'username':'gaphopper','password':'wrong'}).status_code==401
    assert c.post('/api/v1/auth/login',json={'username':'gaphopper','password':'wrong'}).status_code==429


def test_json_job_identification_and_configured_choice(client):
    c,ai,_=client
    response=c.post('/api/v1/identify-job',json={'text':'Álláshirdetés: Adatok kezelése és kapcsolattartás.','ai':{'provider':'openai','model':'chosen'}})
    assert response.status_code==200
    data=response.json()
    assert data['selected_job_id']=='j1'
    assert data['matches'][0]['label']=='Munkakör 1'
    assert data['extracted_tasks']==['Adatok kezelése']
    assert ai.calls[-1][0].model=='chosen'


def test_competency_text_file_identification(client):
    c,_,_=client
    response=c.post('/api/v1/identify-competencies',files={'file':('cv.txt','CV tapasztalat: Adatok kezelése rendszeresen.'.encode(),'text/plain')})
    assert response.status_code==200
    data=response.json()
    assert data['source_filename']=='cv.txt'
    assert data['user_competency_map']==['k2']
    assert data['matches'][0]['evidence']==['Adatok kezelése']


def test_docx_tables_and_html_supported(client):
    from docx import Document
    c,_,_=client
    doc=Document();doc.add_paragraph('Szakmai tapasztalat és készségek.');table=doc.add_table(rows=1,cols=1);table.cell(0,0).text='Adatok kezelése'
    buffer=BytesIO();doc.save(buffer)
    for name,raw in [('cv.docx',buffer.getvalue()),('cv.html',b'<html><script>ignore this</script><p>CV tapasztalat: Adatok kezel&#233;se rendszeresen.</p></html>')]:
        response=c.post('/api/v1/identify-competencies',files={'file':(name,raw)})
        assert response.status_code==200
        assert response.json()['user_competency_map']==['k2']


@pytest.mark.parametrize('body', [{'text':'too short'}, {'text':' '}, {'text':'x'*60001}, {'text':'valid longer text here','ai':{'provider':'unknown','model':'x'}}])
def test_document_bad_json(client,body):
    c,_,_=client
    assert c.post('/api/v1/identify-job',json=body).status_code==422


def test_document_upload_failures(client):
    from pypdf import PdfWriter
    c,_,_=client
    for filename,raw,status in [('cv.exe',b'bad',415),('cv.docx',b'bad',422),('cv.txt',b'a'*(8*1024*1024+1),413),('cv.txt',b'a'*60001,413)]:
        assert c.post('/api/v1/identify-job',files={'file':(filename,raw)}).status_code==status
    assert c.post('/api/v1/identify-job',data={'text':'Adatok kezelése és további tevékenységek.'},files={'file':('cv.txt',b'other text')}).status_code==422
    assert c.post('/api/v1/identify-job',data={'text':'Adatok kezelése és további tevékenységek.','provider':'openai'},files={'unused':('',b'')}).status_code==422
    pdf=PdfWriter();pdf.add_blank_page(width=100,height=100);buffer=BytesIO();pdf.write(buffer)
    assert c.post('/api/v1/identify-job',files={'file':('scan.pdf',buffer.getvalue())}).status_code==422
    assert c.post('/api/v1/identify-job',files={'file':('cv.txt',b'a'*(9*1024*1024))}).status_code==413


def test_pdf_text_supported(client):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    from services.api.documents import parse_file
    writer=PdfWriter();page=writer.add_blank_page(width=600,height=800)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 50 750 Td (Professional experience: data processing and teamwork.) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    buffer=BytesIO();writer.write(buffer)
    assert 'data processing' in parse_file('cv.pdf',buffer.getvalue())


def test_fabricated_ids_or_evidence_rejected(client,monkeypatch):
    c,ai,_=client
    original=ai.ask
    def bad(*args,**kwargs):
        output=original(*args,**kwargs)
        output.matches[0].id='invented'
        return output
    monkeypatch.setattr(ai,'ask',bad)
    assert c.post('/api/v1/identify-job',json={'text':'Adatok kezelése, kapcsolattartás és ügyintézés.'}).status_code==502
    def bad_quote(*args,**kwargs):
        output=original(*args,**kwargs)
        output.matches[0].evidence=['This is invented evidence']
        return output
    monkeypatch.setattr(ai,'ask',bad_quote)
    assert c.post('/api/v1/identify-competencies',json={'text':'Adatok kezelése, kapcsolattartás és ügyintézés.'}).status_code==502


def test_empty_matches_are_valid(client,monkeypatch):
    from services.api.models import JobIdentification
    c,ai,_=client
    monkeypatch.setattr(ai,'ask',lambda *a,**kw:JobIdentification(matches=[]))
    response=c.post('/api/v1/identify-job',json={'text':'Egy nem szakmai szöveg mindenféle állítás nélkül.'})
    assert response.status_code==200
    assert response.json()['selected_job_id'] is None
    assert response.json()['warnings']


def test_validation_does_not_echo_password(client):
    c,_,_=client
    response=c.post('/api/v1/admin/users',json={'username':'new-user','password':'secret'})
    assert response.status_code==422
    assert 'secret' not in response.text
