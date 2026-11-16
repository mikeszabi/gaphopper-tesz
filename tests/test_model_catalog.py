from types import SimpleNamespace
import pytest
from gaphopper import model_catalog, config, web


def test_official_openai_only_returns_listed_chat_models(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','test-key')
    monkeypatch.setattr(model_catalog.LocalLLM,'models',lambda self:['gpt-test','text-embedding-3-small','whisper-1','gpt-test-image','gpt-test-pro','o3','not-a-model'])
    assert model_catalog.list_models('openai',base_url='https://api.openai.com/v1')==['gpt-test','o3']
    # Custom endpoints may expose arbitrary legitimate chat model names.
    monkeypatch.setattr(model_catalog.LocalLLM,'models',lambda self:['shared-local','some-qwen','embedding-model'])
    assert model_catalog.list_models('openai',base_url='http://custom/v1')==['shared-local','some-qwen']


def test_gemini_pagination_actions_and_nontext_filter(monkeypatch):
    from google import genai
    monkeypatch.setenv('GEMINI_API_KEY','test-key')
    models=[SimpleNamespace(name='models/gemini-test',supported_actions=['generateContent']),
            SimpleNamespace(name='models/embed-test',supported_actions=['embedContent']),
            SimpleNamespace(name='models/gemini-tts',supported_actions=['generateContent']),
            SimpleNamespace(name='models/lyria-test',supported_actions=['generateContent']),
            SimpleNamespace(name='models/nano-banana-test',supported_actions=['generateContent'])]
    closed=[]
    monkeypatch.setattr(genai,'Client',lambda **kw:SimpleNamespace(models=SimpleNamespace(list=lambda:iter(models)),close=lambda:closed.append(True)))
    assert model_catalog.list_models('gemini')==['gemini-test']
    assert closed==[True]


def test_anthropic_lifecycle_and_no_fallback(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','test-key')
    rows=[SimpleNamespace(id='claude-live',lifecycle='active'),SimpleNamespace(id='claude-retired',lifecycle='retired')]
    client=SimpleNamespace(models=SimpleNamespace(list=lambda **kw:iter(rows)),close=lambda:None)
    client.with_options=lambda **kw:client
    monkeypatch.setattr(model_catalog,'anthropic_client',lambda:client)
    assert model_catalog.list_models('anthropic')==['claude-live']
    client.models.list=lambda **kw:(_ for _ in ()).throw(RuntimeError('key-secret'))
    with pytest.raises(model_catalog.ModelDiscoveryError) as error:
        model_catalog.list_models('anthropic')
    assert 'key-secret' not in str(error.value)


def test_failed_discovery_cannot_return_saved_or_static_models(monkeypatch):
    config.save_ui_models({'catalog':{'gemini':['old']}})
    def fail(*a,**kw):raise model_catalog.ModelDiscoveryError('No permission')
    monkeypatch.setattr(web,'list_models',fail)
    result=web.discover_models({'provider':'gemini'})
    assert result['ok'] is False and result['models']==[]
    assert web.models_state()['catalog']['gemini']==[]


def test_probe_never_calls_unlisted_model(monkeypatch):
    monkeypatch.setattr(model_catalog,'list_models',lambda *a,**kw:['available'])
    with pytest.raises(model_catalog.ModelDiscoveryError,match='API-listában'):
        model_catalog.verify_model('openai','invented')


def test_failed_probe_preserves_tesz_configuration(monkeypatch):
    before=web.models_state()
    before['steps'][0]['model']='new-model'
    def failed(*a,**kw):raise model_catalog.ModelDiscoveryError('No generation permission')
    monkeypatch.setattr(web,'verify_model',failed)
    with pytest.raises(model_catalog.ModelDiscoveryError):web.save_models(before)
    assert not config.MODELS_PATH.exists()


def test_probe_uses_only_tiny_synthetic_request(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','test-key')
    monkeypatch.setattr(model_catalog,'list_models',lambda *a,**kw:['available'])
    calls=[]
    def complete(self,system,user):
        calls.append((self.cfg.max_tokens,system,user))
        return '{"ok":true}',{}
    monkeypatch.setattr(model_catalog.LocalLLM,'complete',complete)
    model_catalog.verify_model('openai','available',base_url='https://example.com/v1')
    assert calls==[(1024,'Return a JSON object only.','Return {"ok":true}.')]
