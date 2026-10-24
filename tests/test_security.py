import base64
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.parse
from unittest.mock import patch
os.environ.setdefault('BACCHUS_PBKDF2_ITERATIONS','1000')
from server.accounts import current_step, totp
from server.crypto import SecretBox, SecretError, load_box
from server.repository import SQLiteRepository
from tests.test_tenants import Server

def code(secret, offset=0):
    return totp(secret,current_step()+offset)

class MfaTests(Server):
    def enroll(self,session):
        setup=self.call('/api/auth/mfa/setup',session,{})
        self.assertTrue(setup['uri'].startswith('otpauth://totp/BACCHUS%3A'));self.assertIn('<svg',setup['qrSvg'])
        codes=self.call('/api/auth/mfa/enable',session,{'code':code(setup['secret'])})['recoveryCodes']
        return setup['secret'],codes
    def password_login(self,email,password='a-strong-password-1'):
        return self.call('/api/auth/login',body={'email':email,'password':password})
    def test_enroll_login_replay_and_recovery(self):
        _,admin=self.tenant('A店','a@example.com')
        secret,codes=self.enroll(admin)
        self.assertEqual(len(codes),10);self.assertEqual(self.call('/api/auth/mfa',admin)['recoveryCodesLeft'],10)
        first=self.password_login('a@example.com')
        self.assertTrue(first['mfaRequired']);self.assertNotIn('session',first)
        # The code used at enrollment cannot be replayed; the next time step's code works.
        self.assertEqual(self.status('/api/auth/mfa/verify',body={'challenge':first['challenge'],'code':code(secret)}),401)
        session=self.call('/api/auth/mfa/verify',body={'challenge':first['challenge'],'code':code(secret,1)})['session']
        self.assertEqual(self.call('/api/state',session)['refrigerators'],[])
        self.assertEqual(self.status('/api/auth/mfa/verify',body={'challenge':first['challenge'],'code':code(secret,1)}),401)
        # Recovery codes work once each, with or without the hyphen.
        second=self.password_login('a@example.com')
        self.call('/api/auth/mfa/verify',body={'challenge':second['challenge'],'code':codes[0].upper().replace('-','')})
        third=self.password_login('a@example.com')
        self.assertEqual(self.status('/api/auth/mfa/verify',body={'challenge':third['challenge'],'code':codes[0]}),401)
        self.assertEqual(self.call('/api/auth/mfa',session)['recoveryCodesLeft'],9)
        with sqlite3.connect(self.db) as db:
            stored=db.execute("SELECT mfa_secret FROM users WHERE email='a@example.com'").fetchone()[0]
            hashes=[r[0] for r in db.execute('SELECT code_hash FROM recovery_codes')]
        self.assertTrue(stored.startswith('enc:v1:'));self.assertNotIn(secret,stored)
        self.assertFalse(any(c.replace('-','') in h for c in codes for h in hashes))
        audit=[e['action'] for e in self.call('/api/tenant',session)['audit']]
        self.assertIn('auth.mfa_enable',audit);self.assertIn('auth.mfa_failed',audit)
    def test_challenge_attempts_are_limited(self):
        _,admin=self.tenant('A店','a@example.com');secret,_=self.enroll(admin)
        challenge=self.password_login('a@example.com')['challenge']
        for _ in range(4):self.assertEqual(self.status('/api/auth/mfa/verify',body={'challenge':challenge,'code':'000000'}),401)
        # Five wrong codes in a row lock the account like wrong passwords do.
        self.assertEqual(self.status('/api/auth/mfa/verify',body={'challenge':challenge,'code':'000000'}),401)
        self.assertEqual(self.status('/api/auth/mfa/verify',body={'challenge':challenge,'code':code(secret,1)}),401)
    def test_required_mfa_restricts_until_enrolled(self):
        _,admin=self.tenant('A店','a@example.com')
        member=self.add_user(admin,'m@example.com')
        policy={'action':'updateLogin','allowPassword':True,'allowSso':False,'requireMfa':True,'allowDevices':True}
        self.assertEqual(self.status('/api/tenant',admin,policy),409)  # the administrator enrolls first
        self.enroll(admin);self.call('/api/tenant',admin,policy)
        # Sessions opened by password must set up MFA first, including ones already open.
        self.assertEqual(self.status('/api/state',member),403)
        self.assertTrue(self.call('/api/session',member)['mfaSetupRequired'])
        secret,_=self.enroll(member)
        self.assertEqual(self.status('/api/state',member),200)
        self.assertEqual(self.status('/api/auth/mfa/disable',member,{'code':code(secret,1)}),409)
        user=next(u for u in self.call('/api/tenant',admin)['users'] if u['email']=='m@example.com')
        self.assertTrue(user['mfaEnabled'])
        self.call('/api/tenant',admin,{'action':'resetMfa','userId':user['id']})
        self.assertEqual(self.status('/api/session',member),401)
        self.assertFalse(self.password_login('m@example.com').get('mfaRequired',False))
    def test_optional_mfa_can_be_disabled_and_codes_regenerated(self):
        _,admin=self.tenant('A店','a@example.com');secret,codes=self.enroll(admin)
        fresh=self.call('/api/auth/mfa/recovery-codes',admin,{'code':codes[1]})['recoveryCodes']
        self.assertEqual(self.status('/api/auth/mfa/disable',admin,{'code':codes[2]}),401)  # replaced codes no longer work
        self.call('/api/auth/mfa/disable',admin,{'code':fresh[0]})
        self.assertNotIn('mfaRequired',self.password_login('a@example.com'))
    def test_system_admins_always_use_mfa(self):
        created=self.call('/api/system',self.system,{'action':'createSystemAdmin','name':'運営2','email':'ops@example.com'})
        session=self.first_login('ops@example.com',created['temporaryPassword'])
        self.assertTrue(self.call('/api/session',session)['mfaSetupRequired'])
        self.assertEqual(self.status('/api/system',session),403)
        self.enroll(session)
        self.assertEqual(self.status('/api/system',session),200)
        admins=self.call('/api/system',self.system)['systemAdmins']
        target=next(u for u in admins if u['email']=='ops@example.com')
        self.call('/api/system',self.system,{'action':'resetSystemAdminMfa','userId':target['id']})
        self.assertEqual(self.status('/api/session',session),401)

class DeviceTests(Server):
    def test_register_rotate_and_detect_reuse(self):
        _,admin=self.tenant('A店','a@example.com')
        device=self.call('/api/auth/devices',admin,{'name':'花子のiPhone','platform':'ios'})
        first=self.call('/api/auth/device-login',body={'deviceId':device['deviceId'],'deviceToken':device['deviceToken']})
        self.assertNotEqual(first['deviceToken'],device['deviceToken'])
        self.assertEqual(self.call('/api/session',first['session'])['method'],'device')
        second=self.call('/api/auth/device-login',body={'deviceId':device['deviceId'],'deviceToken':first['deviceToken']})
        # Replaying a rotated token suggests a copy exists: the device is unregistered.
        self.assertEqual(self.status('/api/auth/device-login',body={'deviceId':device['deviceId'],'deviceToken':first['deviceToken']}),401)
        self.assertEqual(self.status('/api/auth/device-login',body={'deviceId':device['deviceId'],'deviceToken':second['deviceToken']}),401)
        self.assertEqual(self.call('/api/auth/devices',admin),[])
        self.assertIn('device.reuse_detected',[e['action'] for e in self.call('/api/tenant',admin)['audit']])
        with sqlite3.connect(self.db) as db:self.assertNotIn(second['deviceToken'],'\n'.join(db.iterdump()))
    def test_device_policy_revocation_and_password_reset(self):
        _,admin=self.tenant('A店','a@example.com');member=self.add_user(admin,'m@example.com')
        device=self.call('/api/auth/devices',member,{'name':'太郎のAndroid','platform':'android'})
        self.assertEqual([d['userName'] for d in self.call('/api/tenant',admin)['devices']],['m'])
        user=next(u for u in self.call('/api/tenant',admin)['users'] if u['email']=='m@example.com')
        self.call('/api/tenant',admin,{'action':'resetPassword','userId':user['id']})
        self.assertEqual(self.status('/api/auth/device-login',body=device),401)
        self.call('/api/tenant',admin,{'action':'updateLogin','allowPassword':True,'allowSso':False,'requireMfa':False,'allowDevices':False})
        self.assertEqual(self.status('/api/auth/devices',admin,{'name':'x','platform':'ios'}),403)
        self.assertEqual(self.status('/api/auth/devices',self.system,{'name':'x','platform':'ios'}),409)  # fixed tokens cannot register devices

class AppSsoTests(Server):
    def test_app_sso_returns_to_the_app(self):
        from tests.test_tenants import LoginTests
        _,admin=self.tenant('A店','a@example.com')
        LoginTests.sso_setup(self,admin)
        self.claims={'iss':'https://idp.example','aud':'client-1','exp':9999999999,'sub':'u1','email':'a@example.com'}
        url=urllib.parse.urlsplit(self.call('/api/auth/sso/start',body={'email':'a@example.com','client':'app'})['url'])
        query=urllib.parse.parse_qs(url.query);self.nonce=query['nonce'][0]
        location=LoginTests.callback(self,{'state':query['state'][0],'code':'c'})
        self.assertTrue(location.startswith('bacchus://sso?code='),location)
        session=self.call('/api/auth/exchange',body={'code':urllib.parse.unquote(location.split('code=')[1])})['session']
        self.assertEqual(self.call('/api/session',session)['method'],'sso')
        url=urllib.parse.urlsplit(self.call('/api/auth/sso/start',body={'email':'a@example.com','client':'app'})['url'])
        query=urllib.parse.parse_qs(url.query);self.nonce='wrong'
        self.assertTrue(LoginTests.callback(self,{'state':query['state'][0],'code':'c'}).startswith('bacchus://sso?error='))

class EncryptionTests(unittest.TestCase):
    def test_box(self):
        box=SecretBox([b'k'*32])
        value=box.encrypt('sk-ant-secret','tenant:a:claude_api_key')
        self.assertTrue(value.startswith('enc:v1:'));self.assertNotIn('secret',value)
        self.assertEqual(box.decrypt(value,'tenant:a:claude_api_key'),'sk-ant-secret')
        self.assertNotEqual(value,box.encrypt('sk-ant-secret','tenant:a:claude_api_key'))  # random nonce
        with self.assertRaises(SecretError):box.decrypt(value,'tenant:b:claude_api_key')  # bound to its row
        raw=bytearray(base64.urlsafe_b64decode(value.split(':')[3]+'=='));raw[20]^=1
        with self.assertRaises(SecretError):box.decrypt(value[:value.rindex(':')+1]+base64.urlsafe_b64encode(bytes(raw)).decode().rstrip('='),'tenant:a:claude_api_key')
        self.assertEqual(box.decrypt('plain-legacy-value','x'),'plain-legacy-value')
        with self.assertRaises(SecretError):SecretBox([b'z'*32]).decrypt(value,'tenant:a:claude_api_key')
    def test_rotation_and_migration_of_plain_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            old,new=base64.urlsafe_b64encode(b'o'*32).decode(),base64.urlsafe_b64encode(b'n'*32).decode()
            path=tmp+'/db'
            with patch.dict(os.environ,{'BACCHUS_SECRET_KEY':old,'BACCHUS_SECRET_KEY_PREVIOUS':''}):
                repo=SQLiteRepository(path,load_box(path)[0]);t=repo.create_tenant('T')['id']
                repo.tenant_settings(t).set_setting('openai_api_key','sk-old')
            with sqlite3.connect(path) as db:db.execute("INSERT INTO tenant_settings VALUES (?,?,?)",(t,'sso_client_secret','plain-secret'))
            with patch.dict(os.environ,{'BACCHUS_SECRET_KEY':new,'BACCHUS_SECRET_KEY_PREVIOUS':old}):
                repo=SQLiteRepository(path,load_box(path)[0]);self.assertEqual(repo.rewrap_secrets(),2)
                self.assertEqual(repo.tenant_settings(t).get_setting('openai_api_key'),'sk-old')
                self.assertEqual(repo.tenant_settings(t).get_setting('sso_client_secret'),'plain-secret')
            with patch.dict(os.environ,{'BACCHUS_SECRET_KEY':new,'BACCHUS_SECRET_KEY_PREVIOUS':''}):
                self.assertEqual(SQLiteRepository(path,load_box(path)[0]).tenant_settings(t).get_setting('openai_api_key'),'sk-old')
    def test_key_file_fallback(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'BACCHUS_SECRET_KEY':'','BACCHUS_SECRET_KEY_FILE':''}):
            box,source=load_box(tmp+'/db')
            self.assertEqual(source,'file');self.assertEqual(oct(os.stat(tmp+'/secret.key').st_mode&0o777),'0o600')
            self.assertEqual(load_box(tmp+'/db')[0].current,box.current)

class UpgradeV2Tests(unittest.TestCase):
    def test_v2_database_gains_mfa_and_device_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                db.execute("INSERT INTO metadata VALUES ('schema_version','2')")
                db.execute('CREATE TABLE tenants (id TEXT PRIMARY KEY, name TEXT NOT NULL, workspace TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, ai_monthly_cap INTEGER)')
                db.execute("INSERT INTO tenants VALUES ('t','T','w','2026-01-01',50)")
                db.execute("CREATE TABLE users (id TEXT PRIMARY KEY, tenant_id TEXT REFERENCES tenants(id) ON DELETE CASCADE, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL, role TEXT NOT NULL, password_hash TEXT, must_change_password INTEGER NOT NULL DEFAULT 0, sso_subject TEXT, disabled INTEGER NOT NULL DEFAULT 0, failed_logins INTEGER NOT NULL DEFAULT 0, locked_until TEXT, created_at TEXT NOT NULL, last_login_at TEXT)")
                db.execute("INSERT INTO users (id,tenant_id,email,name,role,created_at) VALUES ('u','t','u@example.com','U','member','2026-01-01')")
                db.execute('CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, expires_at TEXT NOT NULL, ip TEXT NOT NULL, user_agent TEXT NOT NULL)')
                db.execute('CREATE TABLE sso_requests (state_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, nonce TEXT NOT NULL, verifier TEXT NOT NULL, expires_at TEXT NOT NULL)')
            repo=SQLiteRepository(path)
            self.assertEqual(repo.tenant('t'),{'id':'t','name':'T','workspace':'w','createdAt':'2026-01-01'})
            user=repo.user('u');self.assertFalse(user['mfaEnabled']);self.assertEqual(user['mfaLastStep'],0)
            with sqlite3.connect(path) as db:
                self.assertIn('method',[r[1] for r in db.execute('PRAGMA table_info(sessions)')])
                self.assertEqual(db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0],'3')

if __name__=='__main__':unittest.main()
