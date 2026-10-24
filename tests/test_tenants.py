import base64
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch
os.environ.setdefault('BACCHUS_PBKDF2_ITERATIONS','1000')
from server import sso
from server.accounts import parse_env_users
from server.app import create_server
from server.domain import DomainError
from server.repository import SQLiteRepository

PHOTO='data:image/jpeg;base64,'+base64.b64encode(b'\xff\xd8test').decode()

class Server(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=self.tmp.name+'/db'
        self.system='system-token-longer-than-24-characters'
        self.env=patch.dict(os.environ,{'DATABASE_PATH':self.db,'BACCHUS_USERS':json.dumps({self.system:{'name':'運営','role':'system_admin'}}),'BACCHUS_PUBLIC_URL':'https://sake.example',
                                        'GEMINI_API_KEY':'','AI_PROVIDER':''});self.env.start()
        self.http=create_server(port=0);threading.Thread(target=self.http.serve_forever,daemon=True).start();self.url=f'http://127.0.0.1:{self.http.server_port}'
        self.n=0;sso._discovery.clear()
    def tearDown(self):self.http.shutdown();self.http.server_close();self.env.stop();self.tmp.cleanup()
    def call(self,path,token=None,body=None):
        headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})}
        request=urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        with urllib.request.urlopen(request) as response:return json.load(response)
    def status(self,path,token=None,body=None):
        try:self.call(path,token,body);return 200
        except urllib.error.HTTPError as error:return error.code
    def login(self,email,password):
        return self.call('/api/auth/login',body={'email':email,'password':password})['session']
    def first_login(self,email,temporary,password='a-strong-password-1'):
        session=self.login(email,temporary)
        self.call('/api/auth/password',session,{'currentPassword':temporary,'newPassword':password})
        return session
    def tenant(self,name,email):
        result=self.call('/api/system',self.system,{'action':'createTenant','name':name,'adminName':name+'管理者','adminEmail':email})
        return result['tenant'],self.first_login(email,result['temporaryPassword'])
    def add_user(self,admin,email,role='member'):
        result=self.call('/api/tenant',admin,{'action':'createUser','name':email.split('@')[0],'email':email,'role':role})
        return self.first_login(email,result['temporaryPassword'])
    def command(self,token,kind,payload):
        self.n+=1;return self.call('/api/commands',token,dict(id=f'c{self.n}',type=kind,payload=payload))

class LoginTests(Server):
    def test_password_login_first_change_and_lockout(self):
        result=self.call('/api/system',self.system,{'action':'createTenant','name':'A店','adminName':'花子','adminEmail':'Hanako@Example.com'})
        session=self.call('/api/auth/login',body={'email':'hanako@example.com','password':result['temporaryPassword']})
        self.assertTrue(session['mustChangePassword'])
        # Until the temporary password is replaced, only the session and password change work.
        self.assertEqual(self.status('/api/state',session['session']),403);self.assertEqual(self.call('/api/session',session['session'])['role'],'tenant_admin')
        self.assertEqual(self.status('/api/auth/password',session['session'],{'currentPassword':result['temporaryPassword'],'newPassword':'short'}),409)
        self.call('/api/auth/password',session['session'],{'currentPassword':result['temporaryPassword'],'newPassword':'a-strong-password-1'})
        self.assertEqual(self.call('/api/state',session['session'])['refrigerators'],[])
        self.assertEqual(self.status('/api/auth/login',body={'email':'hanako@example.com','password':result['temporaryPassword']}),401)
        for _ in range(5):self.assertEqual(self.status('/api/auth/login',body={'email':'hanako@example.com','password':'wrong-password'}),401)
        self.assertEqual(self.status('/api/auth/login',body={'email':'hanako@example.com','password':'a-strong-password-1'}),401)
        self.assertEqual(self.status('/api/auth/login',body={'email':'nobody@example.com','password':'whatever-123'}),401)
        audit=[e['action'] for e in self.call('/api/tenant',session['session'])['audit']]
        self.assertIn('auth.password_change',audit);self.assertEqual(audit.count('auth.login_failed'),7)
    def test_logout_and_session_storage(self):
        _,admin=self.tenant('A店','a@example.com')
        self.call('/api/auth/logout',admin,{})
        self.assertEqual(self.status('/api/session',admin),401)
        with sqlite3.connect(self.db) as db:dump='\n'.join(db.iterdump())
        self.assertNotIn(admin,dump);self.assertNotIn('a-strong-password-1',dump)

    def sso_setup(self,tenant_admin):
        def fake(req):
            url=req.full_url if hasattr(req,'full_url') else req
            if url.endswith('/.well-known/openid-configuration'):
                return {'issuer':'https://idp.example','authorization_endpoint':'https://idp.example/authorize','token_endpoint':'https://idp.example/token'}
            body=urllib.parse.parse_qs(req.data.decode())
            self.token_requests.append(body)
            claims=dict(self.claims,nonce=self.nonce)
            return {'id_token':'h.'+base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')+'.s'}
        self.token_requests=[];self.claims={}
        self.fetch=patch('server.sso.fetch_json',side_effect=fake);self.fetch.start();self.addCleanup(self.fetch.stop)
        self.call('/api/tenant',tenant_admin,{'action':'updateLogin','allowPassword':True,'allowSso':True,'ssoIssuer':'https://idp.example','ssoClientId':'client-1','ssoClientSecret':'secret-1'})
    def sso_login(self,email):
        url=urllib.parse.urlsplit(self.call('/api/auth/sso/start',body={'email':email})['url'])
        query=urllib.parse.parse_qs(url.query);self.nonce=query['nonce'][0]
        self.assertEqual((url.netloc,query['redirect_uri'][0],query['code_challenge_method'][0]),('idp.example','https://sake.example/api/auth/sso/callback','S256'))
        return self.callback({'state':query['state'][0],'code':'auth-code'})
    def callback(self,query):
        opener=urllib.request.build_opener(type('NoRedirect',(urllib.request.HTTPRedirectHandler,),{'redirect_request':lambda *a:None}))
        try:opener.open(self.url+'/api/auth/sso/callback?'+urllib.parse.urlencode(query))
        except urllib.error.HTTPError as redirect:return redirect.headers['Location']
    def test_sso_login_binds_subject(self):
        tenant,admin=self.tenant('A店','a@example.com');self.sso_setup(admin)
        result=self.call('/api/tenant',admin,{'action':'createUser','name':'太郎','email':'taro@example.com','role':'member'})
        self.claims={'iss':'https://idp.example','aud':'client-1','exp':time.time()+300,'sub':'idp-user-1','email':'TARO@example.com','email_verified':True}
        location=self.sso_login('taro@example.com')
        self.assertTrue(location.startswith('/#sso='),location)
        self.assertEqual(self.token_requests[0]['client_secret'],['secret-1']);self.assertIn('code_verifier',self.token_requests[0])
        code=urllib.parse.unquote(location.removeprefix('/#sso='))
        session=self.call('/api/auth/exchange',body={'code':code})['session']
        self.assertEqual(self.call('/api/session',session)['user'],'太郎');self.assertEqual(self.status('/api/auth/exchange',body={'code':code}),401)
        # The unused temporary password is withdrawn once SSO proved the identity.
        self.assertEqual(self.status('/api/auth/login',body={'email':'taro@example.com','password':result['temporaryPassword']}),401)
        # Later SSO logins must come from the same identity-provider account.
        self.claims=dict(self.claims,sub='someone-else')
        self.assertIn('sso_error',self.sso_login('taro@example.com'))
        for bad in [dict(self.claims,sub='idp-user-1',aud='other-client'),dict(self.claims,sub='idp-user-1',iss='https://evil.example'),dict(self.claims,sub='idp-user-1',exp=time.time()-600)]:
            with self.subTest(claims=bad):
                self.claims=bad;self.assertIn('sso_error',self.sso_login('taro@example.com'))
        self.assertEqual(self.status('/api/auth/sso/start',body={'email':'a-stranger@example.com'}),401)
        self.assertIn('sso_error',self.callback({'state':'forged','code':'x'}))
    def test_sso_only_tenant_blocks_password_and_email_must_match(self):
        tenant,admin=self.tenant('A店','a@example.com');self.sso_setup(admin)
        self.call('/api/tenant',admin,{'action':'updateLogin','allowPassword':False,'allowSso':True})
        created=self.call('/api/tenant',admin,{'action':'createUser','name':'次郎','email':'jiro@example.com','role':'member'})
        self.assertIsNone(created['temporaryPassword'])
        self.assertEqual(self.status('/api/auth/login',body={'email':'a@example.com','password':'a-strong-password-1'}),401)
        self.claims={'iss':'https://idp.example','aud':'client-1','exp':time.time()+300,'sub':'s','email':'not-jiro@example.com'}
        self.assertIn('sso_error',self.sso_login('jiro@example.com'))
        self.assertEqual(self.status('/api/tenant',admin,{'action':'updateLogin','allowPassword':False,'allowSso':False}),409)

class AdministrationTests(Server):
    def test_system_admin_manages_tenants_only(self):
        a,a_admin=self.tenant('A店','a@example.com');b,b_admin=self.tenant('B店','b@example.com')
        overview=self.call('/api/system',self.system)
        self.assertEqual([(t['name'],t['users']) for t in overview['tenants']],[('A店',1),('B店',1)]);self.assertNotIn('audit',json.dumps(overview['tenants']))
        for path in ('/api/state','/api/tenant','/api/ai-settings'):self.assertEqual(self.status(path,self.system),403)
        self.assertEqual(self.status('/api/tenant',self.system,{'action':'createUser','name':'x','email':'x@example.com','role':'member'}),403)
        self.assertEqual(self.status('/api/system',a_admin),403);self.assertEqual(self.status('/api/system',a_admin,{'action':'deleteTenant','tenantId':b['id'],'confirmName':'B店'}),403)
        self.call('/api/system',self.system,{'action':'renameTenant','tenantId':a['id'],'name':'A店（本店）'})
        self.assertEqual(self.call('/api/session',a_admin)['tenant']['name'],'A店（本店）')
        # Recovery: a new administrator for a tenant whose administrators are gone.
        extra=self.call('/api/system',self.system,{'action':'addTenantAdmin','tenantId':a['id'],'name':'復旧','email':'recover@example.com'})
        self.assertEqual(self.call('/api/session',self.login('recover@example.com',extra['temporaryPassword']))['role'],'tenant_admin')
        actions=[e['action'] for e in self.call('/api/system',self.system)['audit']]
        self.assertEqual(actions[:4],['tenant.add_admin','tenant.rename','tenant.create','tenant.create'])
    def test_tenant_deletion_removes_everything(self):
        a,a_admin=self.tenant('A店','a@example.com');_,b_admin=self.tenant('B店','b@example.com')
        member=self.add_user(a_admin,'m@example.com')
        self.command(a_admin,'addFridge',dict(id='f',name='本店'));self.command(member,'receive',dict(name='酒',volume=720,count=1,ids=['b'],fridgeId='f',photo=PHOTO))
        self.assertEqual(self.status('/api/system',self.system,{'action':'deleteTenant','tenantId':a['id'],'confirmName':'違う名前'}),409)
        self.call('/api/system',self.system,{'action':'deleteTenant','tenantId':a['id'],'confirmName':'A店'})
        for token in (a_admin,member):self.assertEqual(self.status('/api/session',token),401)
        self.assertEqual(self.status('/api/auth/login',body={'email':'m@example.com','password':'a-strong-password-1'}),401)
        with sqlite3.connect(self.db) as db:
            for table in ('users','refrigerators','bottles','bottle_photos','events','tenant_settings','ai_usage','audit_log'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table} WHERE tenant_id=?',(a['id'],)).fetchone()[0],0,table)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],1)  # only B店's administrator remains signed in
        self.assertEqual(self.call('/api/session',b_admin)['tenant']['name'],'B店')
        # Sign-in attempts with addresses that no longer exist are recorded in the system log.
        self.assertEqual([e['action'] for e in self.call('/api/system',self.system)['audit'][:2]],['auth.login_failed','tenant.delete'])
    def test_tenant_admin_manages_users_and_fridges(self):
        a,admin=self.tenant('A店','a@example.com');_,b_admin=self.tenant('B店','b@example.com')
        member=self.add_user(admin,'m@example.com')
        self.command(admin,'addFridge',dict(id='f',name='本店'));self.command(admin,'addFridge',dict(id='g',name='倉庫'))
        for kind,payload in [('addFridge',dict(id='h',name='勝手に')),('renameFridge',dict(id='f',name='改名')),('deleteFridge',dict(id='g'))]:
            with self.subTest(kind=kind):self.assertEqual(self.status('/api/commands',member,dict(id='m-'+kind,type=kind,payload=payload)),403)
        self.command(member,'receive',dict(name='酒',volume=720,count=1,ids=['b'],fridgeId='f'))
        self.assertEqual(self.command(member,'move',dict(bottleId='b',version=1,fridgeId='g'))['bottles'][0]['fridgeId'],'g')
        self.assertEqual(self.call('/api/state',b_admin)['refrigerators'],[])
        self.assertEqual(self.status('/api/tenant',member),403)
        users=self.call('/api/tenant',admin)['users'];m=next(u for u in users if u['email']=='m@example.com')
        self.assertNotIn('passwordHash',json.dumps(users))
        # Other tenants' users are out of reach.
        b_user=self.call('/api/tenant',b_admin)['users'][0]
        self.assertEqual(self.status('/api/tenant',admin,{'action':'deleteUser','userId':b_user['id']}),409)
        self.call('/api/tenant',admin,{'action':'updateUser','userId':m['id'],'role':'tenant_admin'})
        self.assertEqual(self.status('/api/session',member),401)  # role changes end sessions
        reset=self.call('/api/tenant',admin,{'action':'resetPassword','userId':m['id']})
        promoted=self.first_login('m@example.com',reset['temporaryPassword'])
        self.assertEqual(self.call('/api/session',promoted)['role'],'tenant_admin')
        self.call('/api/tenant',admin,{'action':'updateUser','userId':m['id'],'disabled':True})
        self.assertEqual(self.status('/api/session',promoted),401)
        me=next(u for u in users if u['email']=='a@example.com')
        self.assertEqual(self.status('/api/tenant',admin,{'action':'deleteUser','userId':me['id']}),409)
        self.assertEqual(self.status('/api/tenant',admin,{'action':'updateUser','userId':me['id'],'role':'member'}),409)
        self.assertEqual(self.status('/api/tenant',admin,{'action':'createUser','name':'x','email':'a@example.com','role':'member'}),409)
        audit=[e['action'] for e in self.call('/api/tenant',admin)['audit']]
        for action in ('user.create','user.update','user.password_reset','inventory.addFridge'):self.assertIn(action,audit)

class AiTests(Server):
    def answer(self,*args,**kwargs):
        return io.BytesIO(json.dumps({'candidates':[{'content':{'parts':[{'text':json.dumps({'name':'試験酒'})}]}}],'usageMetadata':{'promptTokenCount':1200,'candidatesTokenCount':40}}).encode())
    def listing(self,*args,**kwargs):
        return io.BytesIO(json.dumps({'models':[{'name':'models/gemini-3.5-flash-lite','supportedGenerationMethods':['generateContent']}]}).encode())
    def test_keys_are_per_tenant_and_usage_is_limited(self):
        a,a_admin=self.tenant('A店','a@example.com');_,b_admin=self.tenant('B店','b@example.com')
        member=self.add_user(a_admin,'m@example.com')
        with patch('server.recognition.fetch',side_effect=lambda req,timeout:json.load(self.listing())):
            self.assertEqual(self.status('/api/ai-settings',member,{'apiKey':'AIzaTestKey_0123456789abcdef'}),403)
            status=self.call('/api/ai-settings',a_admin,{'provider':'gemini','apiKey':'AIzaTestKey_0123456789abcdef','model':'auto'})
        self.assertEqual(status['model'],'gemini-3.5-flash-lite')
        self.assertTrue(self.call('/api/session',member)['aiConfigured']);self.assertFalse(self.call('/api/session',b_admin)['aiConfigured'])
        self.call('/api/tenant',a_admin,{'action':'setAiLimit','limit':5})
        self.call('/api/system',self.system,{'action':'setAiCap','tenantId':a['id'],'cap':2})
        with patch('server.recognition.fetch',side_effect=lambda req,timeout:json.load(self.answer())):
            for _ in range(2):self.assertEqual(self.call('/api/recognize',member,{'photo':PHOTO})['name'],'試験酒')
            self.assertEqual(self.status('/api/recognize',member,{'photo':PHOTO}),429)
        usage=self.call('/api/tenant',a_admin)['usage']
        self.assertEqual((usage['used'],usage['limit'],usage['cap'],usage['effectiveLimit']),(2,5,2,2))
        ok=next(s for s in usage['summary'] if s['status']=='ok')
        self.assertEqual((ok['count'],ok['inputTokens'],ok['outputTokens']),(2,2400,80))
        self.assertEqual([r['status'] for r in usage['recent']],['rejected','ok','ok'])
        self.assertEqual(self.call('/api/system',self.system)['tenants'][0]['aiThisMonth'],2)
        self.assertIn('settings.ai',[e['action'] for e in self.call('/api/tenant',a_admin)['audit']])
        self.assertEqual(self.status('/api/system',self.system,{'action':'setAiCap','tenantId':a['id'],'cap':-1}),409)

class EnvAndUpgradeTests(unittest.TestCase):
    def test_env_roles(self):
        t=lambda c:c*24
        users=parse_env_users(json.dumps({t('a'):'Legacy',t('b'):{'name':'Old','role':'admin'},t('c'):{'name':'Member'},t('d'):{'name':'Ops','role':'system_admin'}}))
        self.assertEqual([(u['role'],u['tenantId']) for u in users.values()],[('tenant_admin','default'),('tenant_admin','default'),('member','default'),('system_admin',None)])
        for bad in [{t('a'):{'name':'A','role':'owner'}},{t('a'):{'name':''}},{t('a'):{'name':'A','x':1}},{'short':'A'},[t('a')]]:
            with self.subTest(bad=bad),self.assertRaises(ValueError):parse_env_users(json.dumps(bad))
    def test_single_inventory_becomes_default_tenant(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/db'
            state={'schema':1,'refrigerators':[{'id':'f','name':'自宅'}],'bottles':[dict(id='b',name='酒',brewery='',category='',productionDate='',volume=720,remaining=540,fridgeId='f',shelf='',photo=PHOTO,receivedAt='2026-01-01T00:00:00+00:00',openedAt='2026-01-02T00:00:00+00:00',version=2)],
                   'events':[dict(id='e1',type='consume',at='2026-01-02T00:00:00+00:00',actor='Alice',bottleId='b',name='酒',amount=180,before=dict(id='b',photo=PHOTO,remaining=720,version=1),after=dict(id='b',photo=PHOTO,remaining=540,version=2))]}
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)');db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                db.execute('INSERT INTO state VALUES (1,?)',(json.dumps(state),));db.execute("INSERT INTO metadata VALUES ('workspace','old-workspace'),('setting:gemini_api_key','kept')")
            repo=SQLiteRepository(path)
            read=repo.read('default')
            self.assertEqual(read['bottles'],state['bottles']);self.assertEqual(read['events'][0]['before'],{'id':'b','remaining':720,'version':1})
            self.assertEqual(repo.tenant('default')['workspace'],'old-workspace');self.assertEqual(repo.tenant_settings('default').get_setting('gemini_api_key'),'kept')
            self.assertIsNone(repo.get_setting('gemini_api_key'))
            self.assertEqual(SQLiteRepository(path).read('default'),read)
            self.assertEqual(repo.execute('default',dict(id='e2',type='consume',actor='Bob',payload=dict(bottleId='b',version=2)))['bottles'][0]['remaining'],360)
            with self.assertRaises(DomainError):repo.execute('default',dict(id='x',type='deleteFridge',actor='a',payload=dict(id='missing')))
    def test_tenant_documents_are_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/db';state={'schema':1,'refrigerators':[{'id':'f','name':'A'}],'bottles':[],'events':[]}
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                db.execute('CREATE TABLE tenants (id TEXT PRIMARY KEY, name TEXT NOT NULL, workspace TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, data TEXT NOT NULL)')
                db.execute('CREATE TABLE users (id TEXT PRIMARY KEY, tenant_id TEXT, name TEXT NOT NULL, role TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)')
                db.execute("INSERT INTO tenants VALUES ('t1','T','w1','2026-01-01',?)",(json.dumps(state),))
            repo=SQLiteRepository(path)
            self.assertEqual(repo.read('t1'),state);self.assertEqual(repo.tenant('t1')['workspace'],'w1')

if __name__=='__main__':unittest.main()
