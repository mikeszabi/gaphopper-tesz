import pytest
from fastapi.testclient import TestClient
from services.api.ai import AI, AIConfigurationError, credentials_available
from services.api.accounts import MemoryState
from services.api.main import create_app
from services.api.models import AIChoice, CompetencyIdentification
from services.tests.test_services import Repo


def clear_ai_environment(monkeypatch):
    for name in ['SERVICE_AI_MODEL','SERVICE_AI_PROVIDER','ANTHROPIC_API_KEY','GEMINI_API_KEY','GOOGLE_API_KEY','OPENAI_API_KEY']:
        monkeypatch.delenv(name,raising=False)


def test_missing_model_has_specific_actionable_error(monkeypatch):
    clear_ai_environment(monkeypatch)
    with TestClient(create_app(Repo(),AI(),MemoryState())) as client:
        client.headers['X-Gaphopper-Client']='web'
        client.post('/api/v1/auth/login',json={'username':'gaphopper','password':'gaphopper'})
        response=client.post('/api/v1/identify-competencies',json={'text':'Adatok kezelése és szakmai kapcsolattartás.'})
        assert response.status_code==422
        assert response.json()['code']=='ai_model_missing'
        assert response.json()['config_url']=='/config.html'
        assert 'mentése' in response.json()['detail']


@pytest.mark.parametrize('provider,variable', [('anthropic','ANTHROPIC_API_KEY'),('gemini','GEMINI_API_KEY'),('openai','OPENAI_API_KEY')])
def test_missing_provider_key_is_reported_before_sdk_call(monkeypatch,provider,variable):
    clear_ai_environment(monkeypatch)
    with pytest.raises(AIConfigurationError) as error:
        AI().ask(AIChoice(provider=provider,model='test-model'),'instruction',{},CompetencyIdentification)
    assert error.value.code=='ai_key_missing'
    assert variable in str(error.value)


def test_config_shows_presence_without_exposing_key_and_save_clears_missing_model(monkeypatch):
    clear_ai_environment(monkeypatch)
    monkeypatch.setenv('GEMINI_API_KEY','private-key-not-for-output')
    with TestClient(create_app(Repo(),AI(),MemoryState())) as client:
        client.headers['X-Gaphopper-Client']='web'
        client.post('/api/v1/auth/login',json={'username':'gaphopper','password':'gaphopper'})
        response=client.get('/api/v1/config')
        assert response.json()['credentials_available']=={'anthropic':False,'gemini':True,'openai':False}
        assert 'private-key-not-for-output' not in response.text
        client.put('/api/v1/config',json={'provider':'openai','model':'configured-model'})
        response=client.post('/api/v1/identify-competencies',json={'text':'Adatok kezelése és szakmai kapcsolattartás.'})
        assert response.status_code==422 and response.json()['code']=='ai_key_missing'
        assert 'OPENAI_API_KEY' in response.json()['detail']


def test_google_api_key_alias_is_recognized(monkeypatch):
    clear_ai_environment(monkeypatch)
    monkeypatch.setenv('GOOGLE_API_KEY','private-google-key')
    assert credentials_available()['gemini'] is True


def test_live_catalog_and_rejected_probe_preserve_saved_config(monkeypatch):
    from services.api import main
    from gaphopper.model_catalog import ModelDiscoveryError
    clear_ai_environment(monkeypatch)
    monkeypatch.setattr(main,'list_models',lambda *a,**kw:['only-real-model'])
    with TestClient(create_app(Repo(),AI(),MemoryState())) as client:
        client.headers['X-Gaphopper-Client']='web'
        client.post('/api/v1/auth/login',json={'username':'gaphopper','password':'gaphopper'})
        response=client.get('/api/v1/models?provider=gemini')
        assert response.json()['models']==['only-real-model']
        def unavailable(*a,**kw):raise ModelDiscoveryError('Model not available')
        monkeypatch.setattr(main,'verify_model',unavailable)
        assert client.put('/api/v1/config',json={'provider':'gemini','model':'invented'}).status_code==422
        assert client.get('/api/v1/config').json()['default_model']==''
        monkeypatch.setattr(main,'list_models',unavailable)
        assert client.get('/api/v1/models?provider=gemini').status_code==422
