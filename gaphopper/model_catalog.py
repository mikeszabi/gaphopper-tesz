"""Live, credential-specific model discovery. Never supplies fallback model IDs."""
import os
from urllib.parse import urlparse

from .config import StepModel
from .llm import LocalLLM, anthropic_client


class ModelDiscoveryError(RuntimeError):
    pass


def _text_model(model_id):
    name = model_id.casefold()
    return not any(part in name for part in
                   ('embedding','embed-','imagen','image','veo-','video','tts','audio','realtime',
                    'transcri','whisper','moderation','aqa','robotics','computer-use','deep-research',
                    'lyria','nano-banana','antigravity','customtools'))


def list_models(provider, *, base_url='', api_key_env='OPENAI_API_KEY', local_api_key=''):
    """List models from the configured account/endpoint, with SDK pagination.

    The list confirms API visibility, not remaining quota or future generation success.
    Non-text models are excluded. No cached/static catalog is accepted as evidence.
    """
    client = None
    try:
        if provider == 'anthropic':
            if not os.getenv('ANTHROPIC_API_KEY','').strip():
                raise ModelDiscoveryError('Hiányzik az ANTHROPIC_API_KEY a szerveren.')
            client = anthropic_client().with_options(timeout=20,max_retries=0)
            try:
                models = list(client.models.list(limit=100))
            except Exception as exc:
                from .llm import drop_workspace, is_bad_workspace, use_dotenv_key
                if is_bad_workspace(exc):
                    drop_workspace()
                elif not (getattr(exc,'status_code',None)==401 and use_dotenv_key()):
                    raise
                client.close()
                client = anthropic_client().with_options(timeout=20,max_retries=0)
                models = list(client.models.list(limit=100))
            ids = [m.id for m in models if getattr(m,'lifecycle',None) not in ('retired','deprecated')]
        elif provider == 'gemini':
            if not any(os.getenv(k,'').strip() for k in ('GEMINI_API_KEY','GOOGLE_API_KEY')):
                raise ModelDiscoveryError('Hiányzik a GEMINI_API_KEY vagy GOOGLE_API_KEY a szerveren.')
            from google import genai
            from google.genai import types
            client = genai.Client(http_options=types.HttpOptions(timeout=20000))
            ids = [m.name.removeprefix('models/') for m in client.models.list()
                   if m.name and 'generateContent' in (m.supported_actions or [])]
        elif provider in ('openai','local'):
            url = base_url or ('https://api.openai.com/v1' if provider=='openai' else '')
            key = os.getenv(api_key_env,'') if provider=='openai' else local_api_key
            if provider=='openai' and not key.strip():
                raise ModelDiscoveryError(f'Hiányzik a(z) {api_key_env} API-kulcs a szerveren.')
            ids = LocalLLM(StepModel(provider,'list-only',None,1),url,key,timeout=20,
                           use_proxy=provider=='openai').models()
            if urlparse(url).hostname=='api.openai.com':
                # The official list includes models for other APIs as well. These
                # applications currently call Chat Completions, not Responses.
                ids = [m for m in ids if m.startswith(('gpt-','chatgpt-','o1','o3','o4'))
                       and not any(x in m for x in ('-pro','codex'))]
        else:
            raise ModelDiscoveryError('Ehhez a szolgáltatóhoz nincs automatikus modelllista.')
        return sorted({m for m in ids if isinstance(m,str) and m and _text_model(m)})
    except ModuleNotFoundError:
        raise ModelDiscoveryError('Hiányzik a szolgáltató Python-csomagja. Telepítsd az alkalmazás requirements.txt függőségeit a futtatáshoz használt Python-környezetbe.') from None
    except ModelDiscoveryError:
        raise
    except Exception as exc:
        status = getattr(exc,'status_code',None)
        if status in (401,403):
            message = 'A szolgáltató elutasította a kulcsot vagy a modelllista lekérését.'
        else:
            message = 'Nem sikerült lekérni a modelllistát. Ellenőrizd a kulcsot, a végpontot és a hálózati kapcsolatot.'
        raise ModelDiscoveryError(message) from exc
    finally:
        if client is not None and hasattr(client,'close'):
            client.close()


def verify_model(provider, model, *, base_url='', api_key_env='OPENAI_API_KEY', local_api_key=''):
    """One small generation probe on save, never on list refresh. No user data sent."""
    if model not in list_models(provider,base_url=base_url,api_key_env=api_key_env,local_api_key=local_api_key):
        raise ModelDiscoveryError('A modell nincs az aktuális API-listában; válassz a frissített listából.')
    llm = None
    try:
        cfg = StepModel(provider,model,None,1024)
        from .llm import AnthropicLLM, GeminiLLM
        if provider=='anthropic':
            llm=AnthropicLLM(cfg)
            llm.client=llm.client.with_options(timeout=25,max_retries=0)
        elif provider=='gemini':
            from google import genai
            from google.genai import types
            llm=GeminiLLM.__new__(GeminiLLM)
            llm.cfg=cfg
            llm.client=genai.Client(http_options=types.HttpOptions(timeout=25000))
        else:
            llm=LocalLLM(cfg,base_url,os.getenv(api_key_env,'') if provider=='openai' else local_api_key,
                         timeout=25,use_proxy=provider=='openai')
        text,_=llm.complete('Return a JSON object only.','Return {"ok":true}.')
        from .llm import extract_json
        if extract_json(text).get('ok') is not True:
            raise ModelDiscoveryError('A modell nem adott érvényes választ a rövid JSON-próbára.')
    except ModelDiscoveryError:
        raise
    except Exception:
        raise ModelDiscoveryError('A modell nem teljesítette a szöveges JSON-próbahívást. Ellenőrizd a hozzáférést és a kvótát, vagy válassz másik modellt. A beállítás nem lett elmentve.') from None
    finally:
        client=getattr(llm,'client',None)
        if client and hasattr(client,'close'):
            client.close()
