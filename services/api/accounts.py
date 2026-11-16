"""Persistent service accounts/configuration, isolated from the competency graph.
A single locked state node makes user/session/config mutations atomic across replicas.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from fastapi import HTTPException
from neo4j import unit_of_work

SESSION_SECONDS = 8 * 60 * 60


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def password_matches(password, encoded):
    try:
        kind, salt, expected = encoded.split('$')
        if kind != 'scrypt':
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class MemoryState:
    """Explicit test backend only; production always uses Neo4jState."""
    def __init__(self):
        self.data = None
        self.lock = threading.Lock()

    def change(self, initial, operation):
        with self.lock:
            data = json.loads(json.dumps(self.data if self.data is not None else initial))
            result = operation(data)
            self.data = data
            return result


class Neo4jState:
    def __init__(self, repo):
        self.repo = repo
        self.initialized = False
        self.lock = threading.Lock()

    def change(self, initial, operation):
        if not self.initialized:
            with self.lock:
                if not self.initialized:
                    with self.repo.driver.session(database=self.repo.database) as session:
                        session.run("CREATE CONSTRAINT gh_service_state_id IF NOT EXISTS "
                                    "FOR (n:GHServiceState) REQUIRE n.id IS UNIQUE").consume()
                    self.initialized = True
        @unit_of_work(timeout=25)
        def update(tx):
            row = tx.run("MERGE (s:GHServiceState {id:'main'}) "
                         "ON CREATE SET s.payload=$initial, s.revision=0 "
                         "SET s.revision=coalesce(s.revision,0)+1 RETURN s.payload AS payload",
                         initial=json.dumps(initial)).single()
            data = json.loads(row['payload'])
            result = operation(data)
            tx.run("MATCH (s:GHServiceState {id:'main'}) SET s.payload=$payload", payload=json.dumps(data)).consume()
            return result
        with self.repo.driver.session(database=self.repo.database) as session:
            return session.execute_write(update)


class Accounts:
    def __init__(self, backend):
        self.backend = backend
        self.initial = {"users": {"gaphopper": {"username": "gaphopper", "role": "admin", "active": True,
                         "password_hash": password_hash(os.getenv('SERVICE_BOOTSTRAP_PASSWORD', 'gaphopper'))}},
                        "sessions": {}, "config": {}, "failed_logins": {}}

    def change(self, operation):
        return self.backend.change(self.initial, operation)

    @staticmethod
    def public(user):
        return {k:user[k] for k in ('username', 'role', 'active')}

    def login(self, username, password):
        token = secrets.token_urlsafe(32)
        now = time.time()
        def login(data):
            data['sessions'] = {k:v for k,v in data['sessions'].items() if v['expires'] > now}
            failures = data.setdefault('failed_logins', {})
            for key in list(failures):
                if failures[key]['until'] <= now:
                    del failures[key]
            if username not in failures and len(failures) >= 2048:
                return {'error': 429}
            failure = failures.get(username, {})
            if failure.get('count', 0) >= 10:
                return {'error': 429}
            user = data['users'].get(username)
            # Verify a dummy hash as well to avoid a fast nonexistent-user branch.
            verified = password_matches(password, (user or self.initial['users']['gaphopper'])['password_hash'])
            if not user or not user['active'] or not verified:
                failures[username] = {'count':failure.get('count',0)+1, 'until':now+300}
                return {'error': 401}
            failures.pop(username, None)
            # Bound session storage while preserving multiple browser logins.
            own = sorted((k for k,v in data['sessions'].items() if v['username']==username),
                         key=lambda k:data['sessions'][k]['expires'])
            for key in own[:-9]:
                del data['sessions'][key]
            data['sessions'][token_hash(token)] = {'username':username,'expires':now+SESSION_SECONDS}
            return {'user':self.public(user)}
        result = self.change(login)
        if result.get('error'):
            raise HTTPException(result['error'], 'Túl sok hibás kísérlet; próbáld újra 5 perc múlva.' if result['error']==429 else 'Hibás felhasználónév vagy jelszó.')
        return token, result['user']

    def current(self, token):
        if not token:
            raise HTTPException(401, 'Bejelentkezés szükséges.')
        def find(data):
            session = data['sessions'].get(token_hash(token))
            if not session or session['expires'] <= time.time():
                return None
            user = data['users'].get(session['username'])
            return self.public(user) if user and user['active'] else None
        user = self.change(find)
        if not user:
            raise HTTPException(401, 'A munkamenet lejárt vagy érvénytelen.')
        return user

    def logout(self, token):
        self.change(lambda data:data['sessions'].pop(token_hash(token or ''),None))

    def users(self):
        return self.change(lambda data:[self.public(u) for u in sorted(data['users'].values(),key=lambda u:u['username'])])

    def create_user(self, body):
        hashed = password_hash(body.password)
        def create(data):
            if body.username in data['users']:
                raise HTTPException(409, 'A felhasználónév már létezik.')
            user = {'username':body.username,'password_hash':hashed,'role':body.role,'active':True}
            data['users'][body.username] = user
            return self.public(user)
        return self.change(create)

    def update_user(self, username, body):
        hashed = password_hash(body.password) if body.password else None
        def update(data):
            if username not in data['users']:
                raise HTTPException(404, 'Nincs ilyen felhasználó.')
            user = dict(data['users'][username])
            if body.role is not None:
                user['role'] = body.role
            if body.active is not None:
                user['active'] = body.active
            if hashed:
                user['password_hash'] = hashed
            if not any(u['active'] and u['role']=='admin' for name,u in (data['users'] | {username:user}).items()):
                raise HTTPException(409, 'Legalább egy aktív adminisztrátornak maradnia kell.')
            data['users'][username] = user
            # Changing a password or access immediately revokes all that user's sessions.
            data['sessions'] = {k:v for k,v in data['sessions'].items() if v['username']!=username}
            return self.public(user)
        return self.change(update)

    def config(self):
        def read(data):
            return {'providers':['anthropic','gemini','openai'],
                    'default_provider':data['config'].get('default_provider',os.getenv('SERVICE_AI_PROVIDER','anthropic')),
                    'default_model':data['config'].get('default_model',os.getenv('SERVICE_AI_MODEL',''))}
        return self.change(read)

    def save_config(self, body):
        self.change(lambda data:data.update(config={'default_provider':body.provider,'default_model':body.model}))
        return self.config()


class ConfiguredAI:
    def __init__(self, ai, accounts):
        self.ai, self.accounts = ai, accounts

    def choice(self, requested):
        from .models import AIChoice
        if requested is None:
            config = self.accounts.config()
            if config['default_model']:
                requested = AIChoice(provider=config['default_provider'],model=config['default_model'])
        return self.ai.choice(requested)

    def ask(self, *args, **kwargs):
        return self.ai.ask(*args, **kwargs)
