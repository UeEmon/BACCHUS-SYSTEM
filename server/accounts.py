"""Sign-in, roles and administration.

System administrators manage tenants only: create one with its first administrator, rename it,
cap its AI use and delete it. Everything inside a tenant (users, sign-in methods, SSO, AI keys and
limits, fridges) belongs to its tenant administrators. Members use the tenant's inventory."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from . import sso
from .domain import DomainError, string
from .recognition import LabelRecognizer, RecognitionError
from .repository import DEFAULT_TENANT, now

SYSTEM_ADMIN,TENANT_ADMIN,MEMBER='system_admin','tenant_admin','member'
ROLES=(SYSTEM_ADMIN,TENANT_ADMIN,MEMBER)
TENANT_ROLES=(TENANT_ADMIN,MEMBER)
# Fridge layout and bulk imports belong to tenant administrators; members use every fridge's bottles.
TENANT_ADMIN_COMMANDS={'addFridge','renameFridge','deleteFridge','importPersonal'}
EMAIL=re.compile(r'[^@\s]+@[^@\s]+\.[^@\s]+')
MAX_FAILED_LOGINS,LOCK_MINUTES=5,15

class AccessError(PermissionError):
    pass

class AuthError(PermissionError):
    """Sign-in failed or the session ended; the client should sign in again."""

class LimitError(PermissionError):
    pass

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()

def iterations():
    return int(os.environ.get('BACCHUS_PBKDF2_ITERATIONS','600000'))

def hash_password(password):
    salt=secrets.token_bytes(16);rounds=iterations()
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,rounds)
    return f'pbkdf2_sha256${rounds}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}'

def verify_password(password, stored):
    try:
        scheme,rounds,salt,digest=stored.split('$')
        if scheme!='pbkdf2_sha256':return False
        return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),base64.b64decode(salt),int(rounds)),base64.b64decode(digest))
    except (AttributeError,ValueError):
        return False

def temporary_password():
    return secrets.token_urlsafe(12)

def check_password(password, email=''):
    if not isinstance(password,str) or not 10<=len(password)<=128:raise DomainError('パスワードは10文字以上128文字以下にしてください')
    if password.lower()==email:raise DomainError('メールアドレスと同じパスワードは使えません')
    return password

def email_address(value):
    email=value.strip().lower() if isinstance(value,str) else ''
    if len(email)>254 or not EMAIL.fullmatch(email):raise DomainError('メールアドレスが不正です')
    return email

def month_start():
    return datetime.now(timezone.utc).replace(day=1,hour=0,minute=0,second=0,microsecond=0).isoformat()

def later(**delta):
    return (datetime.now(timezone.utc)+timedelta(**delta)).isoformat()

def parse_env_users(text):
    """BACCHUS_USERS holds fixed-token accounts for bootstrapping and automation; they are not edited
    on screen. A token maps to a display name or to {"name", "role"}: system_admin has no tenant,
    tenant_admin and member join the default tenant. A bare name and the former "admin" role are
    tenant administrators; a missing role is a member."""
    users=json.loads(text)
    error=ValueError('BACCHUS_USERS requires JSON {"long-random-token": "display name" or {"name": "display name", "role": "system_admin", "tenant_admin" or "member"}}; minimum token length 24')
    if not isinstance(users,dict):raise error
    parsed={}
    for key,value in users.items():
        if isinstance(value,str):value={'name':value,'role':TENANT_ADMIN}
        if not isinstance(key,str) or len(key)<24 or not isinstance(value,dict) or set(value)-{'name','role'} or not isinstance(value.get('name'),str) or not value['name']:raise error
        role={'admin':TENANT_ADMIN}.get(value.get('role',MEMBER),value.get('role',MEMBER))
        if role not in ROLES:raise error
        parsed[key]=dict(id='env:'+token_hash(key)[:12],name=value['name'],email='',role=role,tenantId=None if role==SYSTEM_ADMIN else DEFAULT_TENANT,source='env',mustChangePassword=False)
    return parsed

class AccountService:
    def __init__(self, repository, env_users=None, public_url=''):
        self.repository=repository
        self.env_users=env_users or {}
        self.public_url=public_url.rstrip('/')
        self.dummy_hash=None
        if any(u['tenantId'] for u in self.env_users.values()) and not repository.tenant(DEFAULT_TENANT):
            repository.create_tenant('既定のテナント',DEFAULT_TENANT)
    def audit(self, actor, action, tenant_id=None, target='', detail=None, ip=''):
        self.repository.audit(tenant_id,actor,action,target,detail,ip)
    def has_system_admin(self):
        return any(u['role']==SYSTEM_ADMIN for u in self.env_users.values()) or bool(self.repository.users(system=True))

    # Sign-in and sessions
    def authenticate(self, header):
        """The caller as {id, name, email, role, tenantId, source, mustChangePassword}, or None."""
        token=header.removeprefix('Bearer ')
        if not token:return None
        user=next((dict(u) for key,u in self.env_users.items() if hmac.compare_digest(key,token)),None)
        if user:return user
        digest=token_hash(token);session=self.repository.session(digest)
        if not session:return None
        user=self.repository.user(session['userId'])
        if session['expiresAt']<now() or not user or user['disabled']:
            self.repository.delete_sessions(token_hash=digest);return None
        # Idle sessions end after BACCHUS_SESSION_HOURS (12 by default); activity extends them, up to 30 days.
        if session['lastSeenAt']<later(minutes=-5):
            self.repository.touch_session(digest,now(),min(later(hours=float(os.environ.get('BACCHUS_SESSION_HOURS','12'))),(datetime.fromisoformat(session['createdAt'])+timedelta(days=30)).isoformat()))
        return dict(id=user['id'],name=user['name'],email=user['email'],role=user['role'],tenantId=user['tenantId'],source='session',sessionHash=digest,mustChangePassword=user['mustChangePassword'])
    def open_session(self, user, ip, user_agent):
        token=secrets.token_urlsafe(32)
        self.repository.create_session(token_hash(token),user['id'],later(hours=float(os.environ.get('BACCHUS_SESSION_HOURS','12'))),ip,user_agent)
        self.repository.update_user(user['id'],lastLoginAt=now(),failedLogins=0,lockedUntil=None)
        return {'session':token,'mustChangePassword':user['mustChangePassword']}
    def login_settings(self, tenant_id):
        settings=self.repository.tenant_settings(tenant_id)
        return {'allowPassword':settings.get_setting('auth_allow_password')!='0','allowSso':settings.get_setting('auth_allow_sso')=='1',
                'ssoIssuer':settings.get_setting('sso_issuer') or '','ssoClientId':settings.get_setting('sso_client_id') or '','ssoSecretSet':bool(settings.get_setting('sso_client_secret'))}
    def login(self, email, password, ip='', user_agent=''):
        email=email.strip().lower() if isinstance(email,str) else ''
        user=self.repository.user(email=email) if email else None
        if not user or not user['passwordHash']:
            # Spend the same time as a real check so the response does not reveal which addresses exist.
            self.dummy_hash=self.dummy_hash or hash_password('x'*16);verify_password(str(password),self.dummy_hash)
            self.audit({'id':'','name':email[:80] or '(空欄)'},'auth.login_failed',user['tenantId'] if user else None,email[:254],{'reason':'unknown' if not user else 'no_password'},ip)
            raise AuthError('メールアドレスまたはパスワードが違います')
        tenant=user['tenantId']
        if user['lockedUntil'] and user['lockedUntil']>now():
            self.audit(user,'auth.login_failed',tenant,email,{'reason':'locked'},ip)
            raise AuthError(f'ログインに{MAX_FAILED_LOGINS}回失敗したため一時的にロックしています。{LOCK_MINUTES}分後に再試行してください')
        if not verify_password(str(password),user['passwordHash']):
            failed=user['failedLogins']+1
            self.repository.update_user(user['id'],failedLogins=failed,lockedUntil=later(minutes=LOCK_MINUTES) if failed>=MAX_FAILED_LOGINS else None)
            self.audit(user,'auth.login_failed',tenant,email,{'reason':'password','failures':failed},ip)
            raise AuthError('メールアドレスまたはパスワードが違います')
        if user['disabled']:
            self.audit(user,'auth.login_failed',tenant,email,{'reason':'disabled'},ip);raise AuthError('このアカウントは無効化されています')
        if tenant and not self.login_settings(tenant)['allowPassword']:
            self.audit(user,'auth.login_failed',tenant,email,{'reason':'password_disabled'},ip);raise AuthError('このテナントではパスワードでのログインは無効です。SSOでログインしてください')
        self.audit(user,'auth.login',tenant,email,{'method':'password'},ip)
        return self.open_session(user,ip,user_agent)
    def logout(self, user, ip=''):
        if user.get('sessionHash'):
            self.repository.delete_sessions(token_hash=user['sessionHash']);self.audit(user,'auth.logout',user['tenantId'],user['email'],None,ip)
    def change_password(self, user, current, new, ip=''):
        if user['source']!='session':raise DomainError('このアカウントのパスワードはありません（環境変数のトークンで接続中）')
        stored=self.repository.user(user['id'])
        if not stored['passwordHash'] or not verify_password(str(current),stored['passwordHash']):raise AuthError('現在のパスワードが違います')
        if new==current:raise DomainError('新しいパスワードは現在と異なるものにしてください')
        self.repository.update_user(user['id'],passwordHash=hash_password(check_password(new,stored['email'])),mustChangePassword=False)
        self.repository.delete_sessions(user_id=user['id'],keep=user['sessionHash'])
        self.audit(user,'auth.password_change',user['tenantId'],user['email'],None,ip)
    def redirect_uri(self):
        if not self.public_url:raise DomainError('SSOにはサーバーの公開URL（環境変数BACCHUS_PUBLIC_URL）の設定が必要です')
        return self.public_url+'/api/auth/sso/callback'
    def start_sso(self, email, ip=''):
        email=email.strip().lower() if isinstance(email,str) else ''
        user=self.repository.user(email=email) if email else None
        settings=self.login_settings(user['tenantId']) if user and user['tenantId'] else None
        if not user or user['disabled'] or not settings or not settings['allowSso'] or not settings['ssoIssuer']:
            self.audit({'id':user['id'] if user else '','name':user['name'] if user else email[:80]},'auth.sso_failed',user['tenantId'] if user else None,email[:254],{'reason':'not_available'},ip)
            raise AuthError('このメールアドレスではSSOを利用できません。パスワードでログインするか、テナント管理者に確認してください')
        state,nonce,verifier=sso.new_request()
        url=sso.authorization_url(settings['ssoIssuer'],settings['ssoClientId'],self.redirect_uri(),state,nonce,verifier,email)
        self.repository.save_sso_request(token_hash(state),user['id'],nonce,verifier,later(minutes=10))
        return {'url':url}
    def finish_sso(self, state, code, ip=''):
        """Returns a one-time code the page exchanges for a session; the session token never appears in a URL."""
        request=self.repository.take_sso_request(token_hash(state)) if isinstance(state,str) and state else None
        if not request or not isinstance(code,str) or not code:raise AuthError('ログイン要求の有効期限が切れました。もう一度ログインしてください')
        user=self.repository.user(request['userId'])
        tenant_settings=self.repository.tenant_settings(user['tenantId'])
        settings=self.login_settings(user['tenantId'])
        if user['disabled'] or not settings['allowSso']:raise AuthError('SSOでのログインは無効です')
        try:
            claims=sso.verified_claims(settings['ssoIssuer'],settings['ssoClientId'],tenant_settings.get_setting('sso_client_secret') or '',self.redirect_uri(),code,request['verifier'],request['nonce'])
            subject=claims['iss'].rstrip('/')+'|'+claims['sub']
            if user['ssoSubject']:
                if not hmac.compare_digest(user['ssoSubject'],subject):raise sso.SsoError('登録済みのSSOアカウントと異なります')
            elif str(claims.get('email','')).lower()!=user['email'] or claims.get('email_verified') is False:
                raise sso.SsoError('SSOのメールアドレスが登録と一致しません')
        except sso.SsoError as error:
            self.audit(user,'auth.sso_failed',user['tenantId'],user['email'],{'reason':str(error)},ip);raise AuthError(str(error)) from None
        if not user['ssoSubject']:self.repository.update_user(user['id'],ssoSubject=subject)
        # A temporary password not yet replaced is withdrawn once the user proves their identity by SSO.
        if user['mustChangePassword']:self.repository.update_user(user['id'],passwordHash=None,mustChangePassword=False)
        code=secrets.token_urlsafe(32)
        self.repository.save_login_code(token_hash(code),user['id'],later(minutes=2))
        self.audit(user,'auth.login',user['tenantId'],user['email'],{'method':'sso'},ip)
        return code
    def exchange_code(self, code, ip='', user_agent=''):
        user_id=self.repository.take_login_code(token_hash(code)) if isinstance(code,str) and code else None
        user=self.repository.user(user_id) if user_id else None
        if not user or user['disabled']:raise AuthError('ログインの有効期限が切れました。もう一度ログインしてください')
        return self.open_session(user,ip,user_agent)
    def session_info(self, user):
        tenant=self.repository.tenant(user['tenantId']) if user['tenantId'] else None
        return {'user':user['name'],'email':user['email'],'role':user['role'],'source':user['source'],'mustChangePassword':user['mustChangePassword'],
                'tenant':{'id':tenant['id'],'name':tenant['name']} if tenant else None,'workspace':tenant['workspace'] if tenant else None}

    # Permissions
    @staticmethod
    def require(user, *roles, message='この操作の権限がありません'):
        if user['role'] not in roles:raise AccessError(message)
    @staticmethod
    def require_ready(user):
        if user.get('mustChangePassword'):raise AccessError('初回ログインのため、パスワードを変更してください')
    @staticmethod
    def tenant_of(user):
        if not user['tenantId']:raise AccessError('全体管理者はテナントの在庫・設定を操作できません。テナントの管理者に依頼してください')
        return user['tenantId']
    @staticmethod
    def check_command(user, command):
        if command.get('type') in TENANT_ADMIN_COMMANDS and user['role']!=TENANT_ADMIN:
            raise AccessError('冷蔵庫の管理と在庫の一括追加はテナント管理者のみ可能です')
    def execute(self, user, command, ip=''):
        tenant=self.tenant_of(user);self.check_command(user,command)
        command['actor']=user['name']
        state=self.repository.execute(tenant,command)
        if command.get('type') in TENANT_ADMIN_COMMANDS:
            event=next((e for e in reversed(state['events']) if e['id']==command.get('id')),{})
            self.audit(user,'inventory.'+command['type'],tenant,event.get('name',''),{k:event[k] for k in ('added','skipped') if k in event},ip)
        return state

    # System administration: tenants only
    def system_overview(self, user):
        self.require(user,SYSTEM_ADMIN)
        users,bottles,usage=self.repository.tenant_counts(month_start())
        return {'tenants':[dict(t,users=users.get(t['id'],0),bottles=bottles.get(t['id'],0),aiThisMonth=usage.get(t['id'],0)) for t in self.repository.tenants()],
                'systemAdmins':[self.public_user(u) for u in self.repository.users(system=True)]+[self.public_user(u) for u in self.env_users.values() if u['role']==SYSTEM_ADMIN],
                'audit':self.repository.audit_log(None),'self':user['id']}
    def system_action(self, user, data, ip=''):
        self.require(user,SYSTEM_ADMIN,message='テナントの管理は全体管理者のみ可能です')
        action=data.get('action')
        if action=='createTenant':
            name=self.name(data.get('name'));admin,password=self.new_user(data.get('adminName'),data.get('adminEmail'),TENANT_ADMIN)
            tenant=self.repository.create_tenant(name,admin=admin)
            self.audit(user,'tenant.create',None,tenant['id'],{'name':name,'admin':admin['email']},ip)
            return {'tenant':tenant,'user':self.public_user(dict(admin,tenantId=tenant['id'])),'temporaryPassword':password}
        if action=='createSystemAdmin':
            admin,password=self.new_user(data.get('name'),data.get('email'),SYSTEM_ADMIN)
            self.repository.create_user(dict(admin,tenantId=None))
            self.audit(user,'system_admin.create',None,admin['email'],None,ip)
            return {'user':self.public_user(dict(admin,tenantId=None)),'temporaryPassword':password}
        if action in ('resetSystemAdmin','deleteSystemAdmin'):
            target=self.repository.user(data.get('userId')) if isinstance(data.get('userId'),str) else None
            if not target or target['role']!=SYSTEM_ADMIN:raise DomainError('対象の全体管理者が見つかりません（環境変数の管理者は画面から変更できません）')
            if action=='deleteSystemAdmin':
                if target['id']==user['id']:raise DomainError('自分自身は削除できません')
                self.repository.delete_user(target['id']);self.audit(user,'system_admin.delete',None,target['email'],None,ip);return {}
            return self.reset_password(user,target,None,ip)
        tenant=self.repository.tenant(data.get('tenantId')) if isinstance(data.get('tenantId'),str) else None
        if not tenant:raise DomainError('テナントが見つかりません')
        if action=='renameTenant':
            name=self.name(data.get('name'));self.repository.update_tenant(tenant['id'],name=name)
            self.audit(user,'tenant.rename',None,tenant['id'],{'from':tenant['name'],'to':name},ip);return {}
        if action=='setAiCap':
            cap=self.limit(data.get('cap'));self.repository.update_tenant(tenant['id'],aiMonthlyCap=cap)
            self.audit(user,'tenant.ai_cap',None,tenant['id'],{'name':tenant['name'],'cap':cap},ip);return {}
        if action=='addTenantAdmin':
            # For recovery when a tenant has lost its administrators; day-to-day user management is the tenant's.
            admin,password=self.new_user(data.get('name'),data.get('email'),TENANT_ADMIN)
            self.repository.create_user(dict(admin,tenantId=tenant['id']))
            self.audit(user,'tenant.add_admin',None,tenant['id'],{'name':tenant['name'],'admin':admin['email']},ip)
            self.audit(user,'user.create',tenant['id'],admin['email'],{'role':TENANT_ADMIN,'by':'system_admin'},ip)
            return {'user':self.public_user(dict(admin,tenantId=tenant['id'])),'temporaryPassword':password}
        if action=='deleteTenant':
            if data.get('confirmName')!=tenant['name']:raise DomainError('確認のため、削除するテナント名を正確に入力してください')
            self.repository.delete_tenant(tenant['id'])
            self.audit(user,'tenant.delete',None,tenant['id'],{'name':tenant['name']},ip);return {}
        raise DomainError('未対応の操作です')

    # Tenant administration
    def tenant_overview(self, user):
        self.require(user,TENANT_ADMIN,message='テナントの管理はテナント管理者のみ可能です')
        tenant=self.tenant_of(user)
        users=[self.public_user(u) for u in self.repository.users(tenant)]+[self.public_user(u) for u in self.env_users.values() if u['tenantId']==tenant]
        return {'tenant':self.repository.tenant(tenant),'users':users,'login':dict(self.login_settings(tenant),redirectUri=self.public_url and self.public_url+'/api/auth/sso/callback'),
                'usage':self.usage(tenant),'audit':self.repository.audit_log(tenant),'self':user['id']}
    def tenant_action(self, user, data, ip=''):
        self.require(user,TENANT_ADMIN,message='テナントの管理はテナント管理者のみ可能です')
        tenant=self.tenant_of(user);action=data.get('action')
        if action=='createUser':
            role=data.get('role') if data.get('role') in TENANT_ROLES else None
            if not role:raise DomainError('ロールが不正です')
            # Password sign-in disabled: the user signs in by SSO, so no temporary password is issued.
            password_login=self.login_settings(tenant)['allowPassword']
            created,password=self.new_user(data.get('name'),data.get('email'),role,password_login)
            self.repository.create_user(dict(created,tenantId=tenant))
            self.audit(user,'user.create',tenant,created['email'],{'role':role},ip)
            return {'user':self.public_user(dict(created,tenantId=tenant)),'temporaryPassword':password}
        if action=='updateLogin':
            allow_password,allow_sso=data.get('allowPassword') is True,data.get('allowSso') is True
            settings=self.repository.tenant_settings(tenant);current=self.login_settings(tenant)
            issuer=data.get('ssoIssuer',current['ssoIssuer']);client_id=data.get('ssoClientId',current['ssoClientId']);secret=data.get('ssoClientSecret') or ''
            if issuer and not sso.https(issuer):raise DomainError('IDプロバイダーのURL（issuer）はhttps://で始まる必要があります')
            if not isinstance(client_id,str) or len(client_id)>300 or not isinstance(secret,str) or len(secret)>500:raise DomainError('クライアントIDまたはシークレットが不正です')
            if allow_sso and not (issuer and client_id and (secret or current['ssoSecretSet'])):raise DomainError('SSOを有効にするには、issuer・クライアントID・クライアントシークレットを設定してください')
            if allow_sso:self.redirect_uri()
            if not allow_password and not allow_sso:raise DomainError('パスワードとSSOの少なくとも一方を有効にしてください')
            if issuer and issuer.rstrip('/')!=current['ssoIssuer'].rstrip('/'):sso.discover(issuer)
            for key,value in (('auth_allow_password','1' if allow_password else '0'),('auth_allow_sso','1' if allow_sso else '0'),('sso_issuer',issuer.rstrip('/')),('sso_client_id',client_id)):
                settings.set_setting(key,value)
            if secret:settings.set_setting('sso_client_secret',secret)
            self.audit(user,'settings.login',tenant,'',{'allowPassword':allow_password,'allowSso':allow_sso,'ssoIssuer':issuer,'ssoClientId':client_id,'secretChanged':bool(secret)},ip)
            return self.login_settings(tenant)
        if action=='setAiLimit':
            limit=self.limit(data.get('limit'))
            settings=self.repository.tenant_settings(tenant)
            settings.set_setting('ai_monthly_limit',str(limit)) if limit is not None else settings.delete_setting('ai_monthly_limit')
            self.audit(user,'settings.ai_limit',tenant,'',{'limit':limit},ip);return self.usage(tenant)
        target=self.repository.user(data.get('userId')) if isinstance(data.get('userId'),str) else None
        if not target or target['tenantId']!=tenant:raise DomainError('対象の利用者が見つかりません（環境変数で設定した利用者は画面から変更できません）')
        if action=='updateUser':
            changes={}
            if 'name' in data:changes['name']=self.name(data['name'])
            if 'role' in data:
                if data['role'] not in TENANT_ROLES:raise DomainError('ロールが不正です')
                changes['role']=data['role']
            if 'disabled' in data:changes['disabled']=data['disabled'] is True
            if target['id']==user['id'] and (changes.get('role',TENANT_ADMIN)!=TENANT_ADMIN or changes.get('disabled')):raise DomainError('自分自身の権限変更・無効化はできません')
            self.repository.update_user(target['id'],**changes)
            if changes.get('disabled') or 'role' in changes:self.repository.delete_sessions(user_id=target['id'])
            self.audit(user,'user.update',tenant,target['email'],changes,ip);return {}
        if action=='resetPassword':return self.reset_password(user,target,tenant,ip)
        if action=='unlinkSso':
            self.repository.update_user(target['id'],ssoSubject=None);self.audit(user,'user.unlink_sso',tenant,target['email'],None,ip);return {}
        if action=='deleteUser':
            if target['id']==user['id']:raise DomainError('自分自身は削除できません')
            self.repository.delete_user(target['id']);self.audit(user,'user.delete',tenant,target['email'],None,ip);return {}
        raise DomainError('未対応の操作です')
    def reset_password(self, user, target, tenant, ip):
        password=temporary_password()
        self.repository.update_user(target['id'],passwordHash=hash_password(password),mustChangePassword=True,failedLogins=0,lockedUntil=None)
        self.repository.delete_sessions(user_id=target['id'])
        self.audit(user,'user.password_reset',tenant,target['email'],None,ip)
        return {'user':self.public_user(target),'temporaryPassword':password}

    # AI: keys and limits are per tenant
    def recognizer(self, tenant_id):
        # Environment keys predate tenants and serve only the tenant that inherited the shared inventory.
        return LabelRecognizer(self.repository.tenant_settings(tenant_id),allow_env=tenant_id==DEFAULT_TENANT)
    def ai_status(self, user):
        return self.recognizer(self.tenant_of(user)).status()
    def configure_ai(self, user, data, ip=''):
        self.require(user,TENANT_ADMIN,message='AI設定の変更はテナント管理者のみ可能です')
        tenant=self.tenant_of(user);provider=data.get('provider') or None;recognizer=self.recognizer(tenant)
        if data.get('clear') is True:
            status=recognizer.clear(provider);self.audit(user,'settings.ai_clear',tenant,provider or '',None,ip);return status
        status=recognizer.configure(data.get('apiKey') or None,data.get('model','auto'),provider)
        self.audit(user,'settings.ai',tenant,status['provider'],{'model':status['model'],'auto':status['auto'],'keyChanged':bool(data.get('apiKey'))},ip)
        return status
    def usage(self, tenant_id):
        tenant=self.repository.tenant(tenant_id);since=month_start()
        stored=self.repository.tenant_settings(tenant_id).get_setting('ai_monthly_limit')
        limit=int(stored) if stored else None;cap=tenant['aiMonthlyCap']
        effective=min([v for v in (limit,cap) if v is not None],default=None)
        return {'since':since,'used':self.repository.usage_count(tenant_id,since),'limit':limit,'cap':cap,'effectiveLimit':effective,
                'summary':self.repository.usage_summary(tenant_id,since),'recent':self.repository.recent_usage(tenant_id)}
    def recognize(self, user, photo, ip=''):
        tenant=self.tenant_of(user);recognizer=self.recognizer(tenant)
        status=recognizer.status();usage=self.usage(tenant)
        if usage['effectiveLimit'] is not None and usage['used']>=usage['effectiveLimit']:
            self.repository.record_usage(tenant,user,status['label'],status['model'] or '','rejected')
            raise LimitError(f'今月のAI読み取り回数が上限（{usage["effectiveLimit"]}回）に達しました。手入力で登録するか、テナント管理者に確認してください')
        try:
            result=recognizer.recognize(photo)
        except RecognitionError as error:
            if error.attempt:self.repository.record_usage(tenant,user,error.attempt[0],error.attempt[1],'error')
            raise
        self.repository.record_usage(tenant,user,result['provider'],result['model'],'ok',result['usage']['inputTokens'],result['usage']['outputTokens'])
        return result

    # Helpers
    def new_user(self, name, email, role, with_password=True):
        email=email_address(email)
        if self.repository.user(email=email) or any(u['email']==email for u in self.env_users.values()):raise DomainError('このメールアドレスは既に登録されています')
        password=temporary_password() if with_password else None
        return dict(id=str(uuid.uuid4()),email=email,name=self.name(name),role=role,passwordHash=hash_password(password) if password else None,mustChangePassword=bool(password),createdAt=now()),password
    @staticmethod
    def public_user(u):
        """What screens may show about a user: never password hashes or tokens."""
        return {'id':u['id'],'tenantId':u['tenantId'],'email':u.get('email',''),'name':u['name'],'role':u['role'],'source':u.get('source','db'),
                'disabled':u.get('disabled',False),'hasPassword':bool(u.get('passwordHash')),'ssoLinked':bool(u.get('ssoSubject')),
                'locked':bool(u.get('lockedUntil') and u['lockedUntil']>now()),'mustChangePassword':u.get('mustChangePassword',False),'lastLoginAt':u.get('lastLoginAt')}
    @staticmethod
    def limit(value):
        if value is None or value=='':return None
        if type(value) is not int or not 0<=value<=1000000:raise DomainError('上限は0〜1,000,000の整数で入力してください（空欄で無制限）')
        return value
    @staticmethod
    def name(value):
        result=string(value if isinstance(value,str) else '',80)
        if not result:raise DomainError('名前を入力してください')
        return result
