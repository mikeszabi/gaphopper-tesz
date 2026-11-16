"""Optional Streamlit model page: choices come only from live provider catalogs."""
from __future__ import annotations
import hashlib
import os
import pandas as pd
import streamlit as st
from gaphopper.config import MODELS_PATH, PRICES, PROVIDERS, STEPS, STEP_NAMES, settings, ui_models
from gaphopper.model_catalog import list_models, ModelDiscoveryError


@st.cache_data(ttl=60,max_entries=24)
def _catalog(provider, base_url, key_env, local_key, credential_fingerprint):
    return list_models(provider,base_url=base_url,api_key_env=key_env,local_api_key=local_key)


def page():
    from gaphopper.web import save_models
    st.title('Modellek')
    st.caption('Csak az aktuális API-kulccsal és végponttal lekért modelllistából választhatsz. A mentett modellnév önmagában nem igazolja az elérhetőséget.')
    st.caption('Mentéskor minden különböző kiválasztott modell rövid JSON-próbahívást kap; ez szolgáltatói díjat használhat.')
    s=settings()
    data=ui_models()
    st.subheader('OpenAI-kompatibilis API')
    base_url=st.text_input('API base URL',s.openai_base_url)
    key_env=st.text_input('API-kulcs környezeti változója',s.openai_api_key_env)
    json_mode=st.selectbox('API JSON-kényszer',['object','schema','none'],index=['object','schema','none'].index(s.openai_json_mode))
    timeout=st.number_input('API időkorlát (mp)',min_value=1,max_value=7200,value=int(s.openai_timeout))
    st.subheader('Helyi modell')
    local_url=st.text_input('Helyi base URL',s.local_base_url)
    local_json=st.selectbox('Helyi JSON-kényszer',['object','schema','none'],index=['object','schema','none'].index(s.local_json_mode))
    local_timeout=st.number_input('Helyi időkorlát (mp)',min_value=30,max_value=7200,value=int(s.local_timeout))
    if st.button('Modelllisták frissítése'):
        _catalog.clear()
        st.rerun()
    selections=[]
    valid=True
    fetched={}
    for step in STEPS:
        previous=s.step(step)
        st.subheader(f'{step} – {STEP_NAMES[step]}')
        provider=st.selectbox('Szolgáltató',PROVIDERS,index=PROVIDERS.index(previous.provider),key=f'provider_{step}')
        model=''
        if provider!='manual':
            if provider not in fetched:
                url=base_url if provider=='openai' else local_url
                secret_names={'anthropic':['ANTHROPIC_API_KEY','ANTHROPIC_WORKSPACE_ID'],
                              'gemini':['GEMINI_API_KEY','GOOGLE_API_KEY'],'openai':[key_env],'local':[]}[provider]
                fingerprint=hashlib.sha256('|'.join(os.getenv(k,'') for k in secret_names).encode()).hexdigest()
                try:
                    fetched[provider]=(_catalog(provider,url,key_env,s.local_api_key,fingerprint),None)
                except ModelDiscoveryError as exc:
                    fetched[provider]=([],str(exc))
            ids,error=fetched[provider]
            if error:
                st.error(error)
            if previous.model and previous.model not in ids and provider==previous.provider:
                st.caption(f'Mentett modell: {previous.model} – nem szerepel az aktuális API-listában.')
            model=st.selectbox('Modell',ids,index=ids.index(previous.model) if previous.model in ids else None,
                               placeholder='Válassz az API-modelllistából',disabled=not ids,key=f'model_{step}') or ''
            valid=valid and bool(model)
        effort=st.selectbox('Gondolkodási szint',['','low','medium','high','xhigh','max'],
                            index=['','low','medium','high','xhigh','max'].index(previous.effort),
                            disabled=provider!='anthropic',key=f'effort_{step}')
        thinking=st.checkbox('Gondolkodás kikapcsolása',value=previous.thinking=='off',key=f'thinking_{step}')
        max_tokens=st.number_input('Maximális kimenet',min_value=256,max_value=128000,value=previous.max_tokens,step=256,key=f'tokens_{step}')
        selections.append({'step':step,'provider':provider,'model':model,'effort':effort if provider=='anthropic' else '',
                           'thinking':'off' if thinking else '', 'max_tokens':max_tokens})
    st.subheader('Bírálat (P2)')
    modes=['auto','always','never']
    mode=st.selectbox('Mikor fusson?',modes,index=modes.index(s.p2_mode))
    sample=st.slider('Véletlen minta aránya',0.,1.,float(s.p2_sample),.05)
    if st.button('Mentés',type='primary',disabled=not valid):
        try:
            save_models({'steps':selections,'openai':{'base_url':base_url,'api_key_env':key_env,'json_mode':json_mode,'timeout':timeout},
                         'local':{'base_url':local_url,'model':(data.get('local') or {}).get('model',''),'json_mode':local_json,'timeout':local_timeout},
                         'p2':{'mode':mode,'sample':sample}})
            st.success('Mentve.')
            st.rerun()
        except (ValueError,ModelDiscoveryError) as exc:
            st.error(str(exc))
    if MODELS_PATH.exists() and st.button('Felületi beállítás törlése (vissza a .env-re)'):
        MODELS_PATH.unlink()
        st.rerun()
    with st.expander('Mentett és referenciaárak – nem modellajánlások'):
        st.dataframe(pd.DataFrame([{'modell':k,'input':v[0],'output':v[1],'cache':v[2]} for k,v in (PRICES | (data.get('prices') or {})).items()]),hide_index=True)
