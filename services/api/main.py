import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from gaphopper.llm import LLMError
from gaphopper.model_catalog import list_models, verify_model, ModelDiscoveryError
from starlette.datastructures import UploadFile
from .accounts import Accounts, ConfiguredAI, Neo4jState, SESSION_SECONDS
from .ai import AI, AIConfigurationError, credentials_available
from .documents import MAX_FILE_BYTES, parse_file
from .identification import identify
from .lab import LabData, extra_competencies, gap, recommend
from .models import (AIChoice, CareerRequest, CareerResult, ComparisonRequest, ComparisonResult,
                     DocumentRequest, IdentificationResult, JobRequest, LabGapRequest, LabProfilesRequest,
                     LabRecommendRequest, LabSpaceRequest, LoginRequest, MapResult, UserCreate, UserUpdate)
from .repository import Repository
from .service import Service

log = logging.getLogger(__name__)
COOKIE = 'gh_session'


class BodyLimitMiddleware:
    """Bound uploads before the multipart parser can spool arbitrarily large files."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope,receive,send)
        async def private_send(message):
            if message['type']=='http.response.start' and scope['path'].startswith('/api/'):
                message['headers']=[(k,v) for k,v in message.get('headers',[]) if k.lower()!=b'cache-control']
                message['headers'].append((b'cache-control',b'no-store'))
            await send(message)
        if scope['method'] not in ('POST','PUT','PATCH'):
            return await self.app(scope,receive,private_send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type']=='http.disconnect':
                return
            chunk = message.get('body',b'')
            size += len(chunk)
            if size > MAX_FILE_BYTES + 65536:
                return await JSONResponse({'detail':'A kérés túl nagy; legfeljebb 8 MB-os fájl tölthető fel.'},413)(scope,receive,send)
            chunks.append(chunk)
            if not message.get('more_body',False):
                break
        sent = False
        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {'type':'http.request','body':b''.join(chunks),'more_body':False}
            return await receive()
        return await self.app(scope,replay,private_send)


DOCUMENT_BODY = {'requestBody':{'required':True,'content':{
    'application/json':{'schema':DocumentRequest.model_json_schema(ref_template='#/components/schemas/{model}')},
    'multipart/form-data':{'schema':{'type':'object','properties':{
        'text':{'type':'string','description':'Szöveg vagy fájl: pontosan az egyiket add meg.'},
        'file':{'type':'string','format':'binary'},'provider':{'type':'string','enum':['anthropic','gemini','openai']},
        'model':{'type':'string'}}}}}}}


def create_app(repo=None, ai=None, state_backend=None):
    @asynccontextmanager
    async def lifespan(app):
        if repo is None:
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=False)
        app.state.repo = repo or Repository()
        app.state.ai = ai or AI()
        app.state.accounts = Accounts(state_backend if state_backend is not None else Neo4jState(app.state.repo))
        app.state.lab = LabData(app.state.repo)
        yield
        if repo is None:
            app.state.repo.close()

    app = FastAPI(title='Gaphopper kompetenciaszolgáltatások',version='1.1.0',lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware)

    def csrf(request: Request):
        if request.method in ('POST','PUT','PATCH','DELETE') and request.headers.get('X-Gaphopper-Client')!='web':
            raise HTTPException(403, 'A munkamenetes módosításhoz X-Gaphopper-Client: web fejléc szükséges.')

    def authorize(request: Request):
        token = os.getenv('SERVICE_API_TOKEN','')
        bearer = request.headers.get('authorization','')
        if bearer:
            if token and secrets.compare_digest(bearer,f'Bearer {token}'):
                return {'username':'api-client','role':'api','active':True}
            raise HTTPException(401,'Hibás API-token.')
        csrf(request)
        return request.app.state.accounts.current(request.cookies.get(COOKIE))

    def admin(request: Request):
        csrf(request)
        user = request.app.state.accounts.current(request.cookies.get(COOKIE))
        if user['role']!='admin':
            raise HTTPException(403,'Adminisztrátori jogosultság szükséges.')
        return user

    def service(request: Request, _=Depends(authorize)):
        return Service(request.app.state.repo,ConfiguredAI(request.app.state.ai,request.app.state.accounts))

    @app.exception_handler(Exception)
    @app.exception_handler(LLMError)
    async def unexpected(request, exc):
        log.warning('Service dependency failed: %s',type(exc).__name__)
        return JSONResponse(status_code=502,content={'detail':'Az adatbázis vagy az AI-szolgáltató nem adott érvényes választ. Ellenőrizd a szerver beállításait és próbáld újra.'})

    @app.exception_handler(RequestValidationError)
    async def bad_request(request, exc):
        # Do not echo passwords, CVs or other raw input in validation errors.
        return JSONResponse(status_code=422,content={'detail':[{'loc':e['loc'],'msg':e['msg']} for e in exc.errors()]})

    @app.exception_handler(ModelDiscoveryError)
    async def model_discovery_error(request, exc):
        return JSONResponse(status_code=422, content={'detail':str(exc),'code':'model_unavailable'})

    @app.exception_handler(AIConfigurationError)
    async def ai_configuration_error(request, exc):
        return JSONResponse(status_code=422, content={'detail':str(exc),'code':exc.code,'config_url':'/config.html'})

    @app.exception_handler(ValueError)
    async def invalid_config(request, exc):
        if isinstance(exc,ValidationError):
            return JSONResponse(status_code=502,content={'detail':'Az AI válasza nem felel meg a kimeneti sémának.'})
        log.warning('Service processing failed: %s',type(exc).__name__)
        return JSONResponse(status_code=502,content={'detail':'Az elemzés feldolgozása hibás eredménnyel zárult. Próbáld újra; ismétlődő hiba esetén ellenőrizd a szerver naplóját.'})

    @app.get('/healthz')
    def health():
        return {'status':'ok'}

    @app.post('/api/v1/auth/login',dependencies=[Depends(csrf)])
    def login(body:LoginRequest,request:Request,response:Response):
        token,user = request.app.state.accounts.login(body.username,body.password)
        secure = os.getenv('SERVICE_COOKIE_SECURE','1' if os.getenv('K_SERVICE') else '0')=='1'
        response.set_cookie(COOKIE,token,max_age=SESSION_SECONDS,httponly=True,secure=secure,samesite='strict',path='/')
        response.headers['Cache-Control']='no-store'
        return user

    @app.get('/api/v1/auth/me')
    def me(request:Request,response:Response):
        response.headers['Cache-Control']='no-store'
        return request.app.state.accounts.current(request.cookies.get(COOKIE))

    @app.post('/api/v1/auth/logout',dependencies=[Depends(csrf)])
    def logout(request:Request,response:Response):
        request.app.state.accounts.logout(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE,path='/')
        return {'status':'ok'}

    @app.get('/api/v1/admin/users',dependencies=[Depends(admin)])
    def users(request:Request):
        return {'items':request.app.state.accounts.users()}

    @app.post('/api/v1/admin/users',status_code=201,dependencies=[Depends(admin)])
    def create_user(body:UserCreate,request:Request):
        return request.app.state.accounts.create_user(body)

    @app.patch('/api/v1/admin/users/{username}',dependencies=[Depends(admin)])
    def update_user(username:str,body:UserUpdate,request:Request):
        return request.app.state.accounts.update_user(username,body)

    @app.get('/readyz',dependencies=[Depends(authorize)])
    def ready(request:Request):
        try:
            request.app.state.repo.ready()
            return {'status':'ready'}
        except Exception:
            raise HTTPException(503,'A Neo4j nem érhető el.') from None

    @app.get('/api/v1/config',dependencies=[Depends(authorize)])
    def config(request:Request):
        return request.app.state.accounts.config() | {'credentials_available':credentials_available()}

    @app.put('/api/v1/config',dependencies=[Depends(admin)])
    def save_config(body:AIChoice,request:Request):
        verify_model(body.provider,body.model,base_url=os.getenv('OPENAI_BASE_URL','https://api.openai.com/v1'))
        return request.app.state.accounts.save_config(body) | {'credentials_available':credentials_available()}

    @app.get('/api/v1/models',dependencies=[Depends(admin)])
    def available_models(provider:str=Query(pattern='^(anthropic|gemini|openai)$')):
        return {'provider':provider,'models':list_models(provider,base_url=os.getenv('OPENAI_BASE_URL','https://api.openai.com/v1')),'source':'provider_api'}

    @app.get('/api/v1/jobs')
    def jobs(q:str=Query(default='',max_length=200),offset:int=Query(default=0,ge=0),
             limit:int=Query(default=50,ge=1,le=200),s:Service=Depends(service)):
        from .service import norm
        rows=[j for j in s.repo.jobs() if norm(q) in norm(j['title']+' '+(j.get('title_en') or '')+' '+j['id'])]
        return {'items':rows[offset:offset+limit],'total':len(rows)}

    @app.get('/api/v1/competencies')
    def competencies(s:Service=Depends(service)):
        return {'items':s.repo.competencies()}

    @app.post('/api/v1/job-maps',response_model=MapResult)
    def job_maps(body:JobRequest,s:Service=Depends(service)):
        return s.job_map(body)

    @app.post('/api/v1/comparisons',response_model=ComparisonResult)
    def comparisons(body:ComparisonRequest,s:Service=Depends(service)):
        return s.compare(body)

    @app.post('/api/v1/career-suggestions',response_model=CareerResult)
    def career(body:CareerRequest,s:Service=Depends(service)):
        return s.career(body)

    async def document_input(request:Request,_=Depends(authorize)):
        try:
            content_type=request.headers.get('content-type','').split(';')[0]
            if content_type=='application/json':
                return DocumentRequest.model_validate(await request.json()),None
            if content_type!='multipart/form-data':
                raise HTTPException(415,'JSON vagy multipart/form-data kérés szükséges.')
            async with request.form(max_files=1,max_fields=4) as form:
                upload=form.get('file')
                text=form.get('text','')
                if not isinstance(text,str) or bool(text.strip())==bool(upload):
                    raise HTTPException(422,'Pontosan egy szöveget vagy egy fájlt adj meg.')
                filename=None
                if upload:
                    if not isinstance(upload,UploadFile):
                        raise HTTPException(422,'A file mezőben fájl szükséges.')
                    filename=Path((upload.filename or 'document').replace('\\','/')).name
                    # Parsing is CPU-bound; keep it off the event loop.
                    from starlette.concurrency import run_in_threadpool
                    raw=await upload.read(MAX_FILE_BYTES+1)
                    text=await run_in_threadpool(parse_file,filename,raw)
                provider,model=form.get('provider'),form.get('model')
                if bool(provider)!=bool(model):
                    raise HTTPException(422,'A provider és model mezőket együtt add meg.')
                choice=AIChoice(provider=provider,model=model) if provider else None
                return DocumentRequest(text=text,ai=choice),filename
        except (ValidationError,ValueError):
            raise HTTPException(422,'Érvénytelen dokumentumbemenet: 20–60 000 karakter és érvényes modellbeállítás szükséges.') from None

    @app.post('/api/v1/identify-job',response_model=IdentificationResult,openapi_extra=DOCUMENT_BODY)
    def identify_job(document=Depends(document_input),s:Service=Depends(service)):
        return identify(s,document[0],'job',document[1])

    @app.post('/api/v1/identify-competencies',response_model=IdentificationResult,openapi_extra=DOCUMENT_BODY)
    def identify_competencies(document=Depends(document_input),s:Service=Depends(service)):
        return identify(s,document[0],'competencies',document[1])

    # ------------------------------------------------------------------ kompetencialabor (determinisztikus, gráfból)
    def lab_data(request:Request,_=Depends(authorize)):
        return request.app.state.lab.get()

    def lab_profiles(data,job_ids):
        unknown=[j for j in job_ids if j not in data['profiles']]
        if unknown:
            raise HTTPException(404,detail={'message':'Nincs felbontott profil ezekhez a munkakörökhöz.','ids':unknown})
        return [data['profiles'][j] for j in dict.fromkeys(job_ids)]

    def lab_person(data,person):
        unknown=sorted({c.id for c in person.competencies}-data['competencies'].keys())
        if unknown:
            raise HTTPException(422,detail={'message':'Ismeretlen kompetenciaazonosítók.','ids':unknown})
        return person.model_dump()

    @app.get('/api/v1/lab/jobs')
    def lab_jobs(q:str=Query(default='',max_length=200),data=Depends(lab_data)):
        from .service import norm
        rows=[{'id':p['id'],'title':p['title'],'title_en':p['title_en'],'eqf':p['eqf']['typical'],
               'competencies':len(p['competencies'])} for p in data['profiles'].values()]
        rows=[r for r in rows if norm(q) in norm(r['title']+' '+r['title_en']+' '+r['id'])]
        return {'items':sorted(rows,key=lambda r:r['title']),'total':len(rows)}

    @app.get('/api/v1/lab/competencies')
    def lab_competencies(data=Depends(lab_data)):
        return {'items':sorted(data['competencies'].values(),key=lambda c:(c.get('category') or 'Z',c.get('group') or '',c['label']))}

    @app.post('/api/v1/lab/job-profiles')
    def lab_job_profiles(body:LabProfilesRequest,data=Depends(lab_data)):
        return {'profiles':lab_profiles(data,body.job_ids)}

    @app.post('/api/v1/lab/gap')
    def lab_gap(body:LabGapRequest,data=Depends(lab_data)):
        person=lab_person(data,body.person)
        profiles=lab_profiles(data,body.job_ids)
        return {'jobs':[gap(p,person) for p in profiles],'extra':extra_competencies(profiles,person,data['competencies']),
                'person_eqf':person.get('eqf')}

    @app.post('/api/v1/lab/recommendations')
    def lab_recommendations(body:LabRecommendRequest,data=Depends(lab_data)):
        person=lab_person(data,body.person)
        return recommend(data['profiles'],person,body.limit,body.eqf_window) | {'person_eqf':person.get('eqf')}

    @app.post('/api/v1/lab/space')
    def lab_space(body:LabSpaceRequest,data=Depends(lab_data)):
        from .space import build_space
        person=lab_person(data,body.person) if body.person else None
        profiles=lab_profiles(data,body.job_ids) if body.job_ids else []
        if person:
            gaps=[gap(p,person) for p in profiles]
        else:
            gaps=[{'job_id':p['id'],'title':p['title'],'eqf':p['eqf'],'fit':None,
                   'competencies':[c|{'status':None,'person_eqf':None,'shortfall':None} for c in p['competencies']]}
                  for p in profiles]
        return build_space(data,gaps,person)

    @app.post('/api/v1/lab/identify-person',openapi_extra=DOCUMENT_BODY)
    def lab_identify_person(request:Request,document=Depends(document_input),s:Service=Depends(service)):
        from .person import identify_person
        return identify_person(s,request.app.state.lab.get(),document[0],document[1])

    @app.post('/api/v1/lab/identify-job',response_model=IdentificationResult,openapi_extra=DOCUMENT_BODY)
    def lab_identify_job(request:Request,document=Depends(document_input),s:Service=Depends(service)):
        ids=set(request.app.state.lab.get()['profiles'])
        return identify(s,document[0],'job',document[1],only_ids=ids)

    app.mount('/',StaticFiles(directory=Path(__file__).resolve().parents[1]/'web',html=True),name='web')
    return app


app=create_app()
