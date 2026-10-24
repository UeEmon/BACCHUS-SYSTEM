import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from server.accounts import parse_env_users
from server.app import create_server
from server.domain import DomainError
from server.repository import SQLiteRepository

class TenantHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=self.tmp.name+'/db'
        self.system='system-token-longer-than-24-characters'
        self.env=patch.dict(os.environ,{'DATABASE_PATH':self.db,'BACCHUS_USERS':json.dumps({self.system:{'name':'運営','role':'system_admin'}}),'GEMINI_API_KEY':'','AI_PROVIDER':''});self.env.start()
        self.http=create_server(port=0);threading.Thread(target=self.http.serve_forever,daemon=True).start();self.url=f'http://127.0.0.1:{self.http.server_port}'
        self.n=0
    def tearDown(self):self.http.shutdown();self.http.server_close();self.env.stop();self.tmp.cleanup()
    def call(self,path,token,body=None):
        request=urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with urllib.request.urlopen(request) as response:return json.load(response)
    def status(self,path,token,body=None):
        try:self.call(path,token,body);return 200
        except urllib.error.HTTPError as error:return error.code
    def command(self,token,kind,payload):
        self.n+=1;return self.call('/api/commands',token,dict(id=f'c{self.n}',type=kind,payload=payload))
    def tenant_with_users(self,name):
        tenant=self.call('/api/admin',self.system,{'action':'createTenant','name':name})['tenant']
        admin=self.call('/api/admin',self.system,{'action':'createUser','tenantId':tenant['id'],'name':name+'管理者','role':'tenant_admin'})['token']
        member=self.call('/api/admin',admin,{'action':'createUser','tenantId':tenant['id'],'name':name+'メンバー','role':'member'})['token']
        return tenant,admin,member
    def test_system_admin_builds_tenants_and_they_stay_isolated(self):
        a,a_admin,a_member=self.tenant_with_users('A店');b,b_admin,_=self.tenant_with_users('B店')
        session=self.call('/api/session',a_member)
        self.assertEqual((session['role'],session['tenant']['name']),('member','A店'))
        self.assertNotEqual(session['workspace'],self.call('/api/session',b_admin)['workspace'])
        self.command(a_admin,'addFridge',dict(id='f',name='A冷蔵庫'))
        self.assertEqual([f['name'] for f in self.call('/api/state',a_member)['refrigerators']],['A冷蔵庫'])
        self.assertEqual(self.call('/api/state',b_admin)['refrigerators'],[])
        # A bottle ID or fridge of another tenant is unknown here.
        self.assertEqual(self.status('/api/commands',b_admin,dict(id='x',type='receive',payload=dict(name='酒',volume=720,count=1,ids=['b'],fridgeId='f'))),409)
        overview=self.call('/api/admin',self.system)
        self.assertEqual([t['name'] for t in overview['tenants']],['A店','B店']);self.assertEqual(len(overview['tenants'][0]['users']),2)
        self.assertEqual([t['name'] for t in self.call('/api/admin',a_admin)['tenants']],['A店'])
        # The system administrator manages accounts, not a tenant's sake.
        self.assertEqual(self.status('/api/state',self.system),403)
        self.assertIsNone(self.call('/api/session',self.system)['tenant'])
        with sqlite3.connect(self.db) as db:dump='\n'.join(db.iterdump())
        for token in (a_admin,a_member):self.assertNotIn(token,dump)
    def test_tenant_admin_manages_fridges_member_uses_them(self):
        _,admin,member=self.tenant_with_users('A店')
        self.command(admin,'addFridge',dict(id='f',name='本店'));self.command(admin,'addFridge',dict(id='g',name='倉庫'))
        for kind,payload in [('addFridge',dict(id='h',name='勝手に')),('renameFridge',dict(id='f',name='改名')),('deleteFridge',dict(id='g')),('importPersonal',dict(snapshot=dict(schema=1,refrigerators=[],bottles=[])))]:
            with self.subTest(kind=kind):self.assertEqual(self.status('/api/commands',member,dict(id='m-'+kind,type=kind,payload=payload)),403)
        self.command(member,'receive',dict(name='酒',volume=720,count=1,ids=['b'],fridgeId='f'))
        self.command(member,'move',dict(bottleId='b',version=1,fridgeId='g'))
        state=self.command(member,'consume',dict(bottleId='b',version=2))
        self.assertEqual((state['bottles'][0]['fridgeId'],state['bottles'][0]['remaining']),('g',540))
        self.assertEqual(self.command(admin,'renameFridge',dict(id='f',name='本店（1階）'))['refrigerators'][0]['name'],'本店（1階）')
        self.assertEqual(self.status('/api/commands',admin,dict(id='del-g',type='deleteFridge',payload=dict(id='g'))),409)
        self.assertEqual([f['id'] for f in self.command(admin,'deleteFridge',dict(id='f'))['refrigerators']],['g'])
    def test_account_permissions(self):
        a,a_admin,a_member=self.tenant_with_users('A店');b,b_admin,_=self.tenant_with_users('B店')
        self.assertEqual(self.status('/api/admin',a_member),403)
        self.assertEqual(self.status('/api/admin',a_member,{'action':'createUser','tenantId':a['id'],'name':'x','role':'member'}),403)
        self.assertEqual(self.status('/api/admin',a_admin,{'action':'createUser','tenantId':b['id'],'name':'x','role':'member'}),403)
        self.assertEqual(self.status('/api/admin',a_admin,{'action':'createUser','name':'x','role':'system_admin'}),403)
        self.assertEqual(self.status('/api/admin',a_admin,{'action':'createTenant','name':'C店'}),403)
        b_users=self.call('/api/admin',self.system)['tenants'][1]['users']
        self.assertEqual(self.status('/api/admin',a_admin,{'action':'deleteUser','userId':b_users[0]['id']}),403)
        me=next(u for u in self.call('/api/admin',a_admin)['tenants'][0]['users'] if u['role']=='tenant_admin')
        self.assertEqual(self.status('/api/admin',a_admin,{'action':'deleteUser','userId':me['id']}),409)
        # AI settings are shared by every tenant and only the system administrator changes them.
        for token in (a_admin,a_member):self.assertEqual(self.status('/api/ai-settings',token,{'clear':True}),403)
        self.assertEqual(self.status('/api/ai-settings',self.system,{'clear':True}),200)
        self.assertEqual(self.status('/api/ai-settings',a_member),200)
    def test_reissue_and_delete_revoke_tokens(self):
        _,admin,member=self.tenant_with_users('A店')
        user=next(u for u in self.call('/api/admin',admin)['tenants'][0]['users'] if u['role']=='member')
        fresh=self.call('/api/admin',admin,{'action':'reissueToken','userId':user['id']})['token']
        self.assertEqual(self.status('/api/session',member),401);self.assertEqual(self.call('/api/session',fresh)['user'],'A店メンバー')
        self.call('/api/admin',admin,{'action':'deleteUser','userId':user['id']})
        self.assertEqual(self.status('/api/session',fresh),401)
        self.assertEqual(self.status('/api/admin',self.system,{'action':'deleteUser','userId':'env:whatever'}),409)

class EnvAndUpgradeTests(unittest.TestCase):
    def test_env_roles(self):
        t=lambda c:c*24
        users=parse_env_users(json.dumps({t('a'):'Legacy',t('b'):{'name':'Old','role':'admin'},t('c'):{'name':'Member'},t('d'):{'name':'Ops','role':'system_admin'}}))
        self.assertEqual([(u['role'],u['tenantId']) for u in users.values()],[('tenant_admin','default'),('tenant_admin','default'),('member','default'),('system_admin',None)])
        for bad in [{t('a'):{'name':'A','role':'owner'}},{t('a'):{'name':''}},{t('a'):{'name':'A','x':1}},{'short':'A'},[t('a')]]:
            with self.subTest(bad=bad),self.assertRaises(ValueError):parse_env_users(json.dumps(bad))
    def test_single_inventory_becomes_default_tenant(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/db';state={'schema':1,'refrigerators':[{'id':'f','name':'自宅'}],'bottles':[],'events':[]}
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)');db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                db.execute('INSERT INTO state VALUES (1,?)',(json.dumps(state),));db.execute("INSERT INTO metadata VALUES ('workspace','old-workspace'),('setting:gemini_api_key','kept')")
            repo=SQLiteRepository(path)
            self.assertEqual(repo.read('default'),state);self.assertEqual(repo.tenant('default')['workspace'],'old-workspace');self.assertEqual(repo.get_setting('gemini_api_key'),'kept')
            self.assertEqual(SQLiteRepository(path).tenants(),repo.tenants())
            with self.assertRaises(DomainError):repo.execute('default',dict(id='x',type='deleteFridge',actor='a',payload=dict(id='missing')))

if __name__=='__main__':unittest.main()
