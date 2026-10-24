"""Sign-in, roles and administration.

System administrators manage tenants only: create one with its first administrator, rename it and
delete it; they read the audit log of every tenant. Everything inside a tenant (users, sign-in
methods, MFA policy, SSO, devices, AI keys and limits, fridges) belongs to its tenant
administrators, who read their own tenant's audit log. Members use the tenant's inventory."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from urllib.parse import quote as urllib_quote
from datetime import datetime, timedelta, timezone
from . import qr, sso
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
MAX_MFA_ATTEMPTS=5
PLATFORMS=('ios','android','other')

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

def totp(secret, step):
    """RFC 6238 time-based one-time password: HMAC-SHA1, 30-second steps, 6 digits."""
    digest=hmac.new(base64.b32decode(secret),step.to_bytes(8,'big'),hashlib.sha1).digest()
    offset=digest[-1]&15
    return f'{(int.from_bytes(digest[offset:offset+4],"big")&0x7fffffff)%1000000:06d}'

def current_step():
    return int(datetime.now(timezone.utc).timestamp())//30

def recovery_code():
    alphabet='abcdefghjkmnpqrstuvwxyz23456789'
    raw=''.join(secrets.choice(alphabet) for _ in range(10))
    return raw[:5]+'-'+raw[5:]

def normalized_code(code):
    return re.sub(r'[\s-]','',code).lower() if isinstance(code,str) else ''

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
    def __init__(self, repository, env_users=None, public_url='', app_scheme='bacchus'):
        self.repository=repository
        self.env_users=env_users or {}
        self.public_url=public_url.rstrip('/')
        self.app_scheme=app_scheme
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
        # A password sign-in where MFA is required but not yet set up may only set it up. SSO relies on the
        # identity provider's own MFA; device sign-in already combines the device with its biometric check.
        setup=session['method']=='password' and self.mfa_required(user) and not user['mfaEnabled']
        return dict(id=user['id'],name=user['name'],email=user['email'],role=user['role'],tenantId=user['tenantId'],source='session',sessionHash=digest,method=session['method'],
                    mustChangePassword=user['mustChangePassword'],mfaEnabled=user['mfaEnabled'],mfaSetupRequired=setup)
    def open_session(self, user, ip, user_agent, method):
        token=secrets.token_urlsafe(32)
        self.repository.create_session(token_hash(token),user['id'],later(hours=float(os.environ.get('BACCHUS_SESSION_HOURS','12'))),ip,user_agent,method)
        self.repository.update_user(user['id'],lastLoginAt=now(),failedLogins=0,lockedUntil=None)
        return {'session':token,'mustChangePassword':user['mustChangePassword'] and method=='password'}
    def login_settings(self, tenant_id):
        settings=self.repository.tenant_settings(tenant_id)
        return {'allowPassword':settings.get_setting('auth_allow_password')!='0','allowSso':settings.get_setting('auth_allow_sso')=='1',
                'requireMfa':settings.get_setting('mfa_required')=='1','allowDevices':settings.get_setting('allow_devices')!='0',
                'ssoIssuer':settings.get_setting('sso_issuer') or '','ssoClientId':settings.get_setting('sso_client_id') or '','ssoSecretSet':bool(settings.get_setting('sso_client_secret'))}
    def mfa_required(self, user):
        # Every system administrator signing in by password uses MFA; tenants decide for their users.
        return user['role']==SYSTEM_ADMIN or bool(user['tenantId'] and self.login_settings(user['tenantId'])['requireMfa'])
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
        if user['mfaEnabled']:
            challenge=secrets.token_urlsafe(32)
            self.repository.save_challenge(token_hash(challenge),user['id'],'web',later(minutes=5))
            return {'mfaRequired':True,'challenge':challenge}
        self.audit(user,'auth.login',tenant,email,{'method':'password'},ip)
        return self.open_session(user,ip,user_agent,'password')
    def verify_mfa(self, challenge, code, ip='', user_agent=''):
        digest=token_hash(challenge) if isinstance(challenge,str) and challenge else ''
        pending=self.repository.challenge(digest) if digest else None
        user=self.repository.user(pending['userId']) if pending else None
        if not user or user['disabled'] or (user['lockedUntil'] and user['lockedUntil']>now()):raise AuthError('確認の有効期限が切れました。もう一度ログインしてください')
        if pending['attempts']>MAX_MFA_ATTEMPTS:
            self.repository.delete_challenge(digest);raise AuthError('確認コードの誤りが続いたため、もう一度ログインしてください')
        method=self.check_second_factor(user,code)
        if not method:
            failed=user['failedLogins']+1
            self.repository.update_user(user['id'],failedLogins=failed,lockedUntil=later(minutes=LOCK_MINUTES) if failed>=MAX_FAILED_LOGINS else None)
            self.audit(user,'auth.mfa_failed',user['tenantId'],user['email'],{'failures':failed},ip)
            raise AuthError('確認コードが違います')
        self.repository.delete_challenge(digest)
        self.audit(user,'auth.login',user['tenantId'],user['email'],{'method':'password','mfa':method},ip)
        return self.open_session(user,ip,user_agent,'password')
    def check_second_factor(self, user, code):
        """'totp' or 'recovery' when the code is valid (each usable once), else None."""
        code=normalized_code(code)
        if re.fullmatch(r'\d{6}',code) and user['mfaEnabled']:
            secret=self.mfa_secret(user);now_step=current_step()
            for step in (now_step-1,now_step,now_step+1):
                if step>user['mfaLastStep'] and hmac.compare_digest(totp(secret,step),code):
                    self.repository.update_user(user['id'],mfaLastStep=step);return 'totp'
            return None
        if len(code)==10 and self.repository.use_recovery_code(user['id'],token_hash(code)):return 'recovery'
        return None
    def mfa_secret(self, user):
        box=self.repository.box
        return box.decrypt(user['mfaSecret'],f'user:{user["id"]}:mfa') if box else user['mfaSecret']
    def mfa_setup(self, user, ip=''):
        """Starts enrollment with a new secret; it takes effect only after mfa_enable confirms a code."""
        if user['source']!='session':raise DomainError('環境変数のトークンで接続中のアカウントは多要素認証を設定できません')
        if user['mfaEnabled']:raise DomainError('多要素認証は設定済みです。変更する場合は一度無効にしてください')
        secret=base64.b32encode(secrets.token_bytes(20)).decode()
        box=self.repository.box
        self.repository.update_user(user['id'],mfaSecret=box.encrypt(secret,f'user:{user["id"]}:mfa') if box else secret,mfaLastStep=0)
        label=urllib_quote(f'BACCHUS:{user["email"]}')
        uri=f'otpauth://totp/{label}?secret={secret}&issuer=BACCHUS&algorithm=SHA1&digits=6&period=30'
        return {'secret':secret,'uri':uri,'qrSvg':qr.svg(uri)}
    def mfa_enable(self, user, code, ip=''):
        stored=self.repository.user(user['id'])
        if stored['mfaEnabled'] or not stored['mfaSecret']:raise DomainError('先に多要素認証の設定を開始してください')
        secret=self.mfa_secret(stored);now_step=current_step();code=normalized_code(code)
        step=next((s for s in (now_step-1,now_step,now_step+1) if hmac.compare_digest(totp(secret,s),code)),None)
        if step is None:raise DomainError('確認コードが違います。認証アプリの時刻設定も確認してください')
        self.repository.update_user(user['id'],mfaEnabled=True,mfaLastStep=step)
        codes=self.new_recovery_codes(user['id'])
        self.repository.delete_sessions(user_id=user['id'],keep=user.get('sessionHash',''))
        self.audit(user,'auth.mfa_enable',user['tenantId'],user['email'],None,ip)
        return {'recoveryCodes':codes}
    def mfa_disable(self, user, code, ip=''):
        stored=self.repository.user(user['id'])
        if not stored['mfaEnabled']:raise DomainError('多要素認証は設定されていません')
        if self.mfa_required(stored):raise DomainError('このアカウントでは多要素認証が必須のため無効にできません')
        if not self.check_second_factor(stored,code):raise AuthError('確認コードが違います')
        self.clear_mfa(stored['id'])
        self.audit(user,'auth.mfa_disable',user['tenantId'],user['email'],None,ip)
    def regenerate_recovery_codes(self, user, code, ip=''):
        stored=self.repository.user(user['id'])
        if not stored['mfaEnabled']:raise DomainError('多要素認証は設定されていません')
        if not self.check_second_factor(stored,code):raise AuthError('確認コードが違います')
        self.audit(user,'auth.recovery_codes',user['tenantId'],user['email'],None,ip)
        return {'recoveryCodes':self.new_recovery_codes(user['id'])}
    def new_recovery_codes(self, user_id):
        codes=[recovery_code() for _ in range(10)]
        self.repository.replace_recovery_codes(user_id,[token_hash(normalized_code(c)) for c in codes])
        return codes
    def clear_mfa(self, user_id):
        self.repository.update_user(user_id,mfaEnabled=False,mfaSecret=None,mfaLastStep=0)
        self.repository.replace_recovery_codes(user_id,[])
    def mfa_status(self, user):
        stored=self.repository.user(user['id']) if user['source']=='session' else None
        return {'enabled':bool(stored and stored['mfaEnabled']),'required':bool(stored and self.mfa_required(stored)),
                'recoveryCodesLeft':self.repository.recovery_codes_left(user['id']) if stored and stored['mfaEnabled'] else 0}

    # Devices: the mobile app keeps a device token behind the phone's biometric check.
    def register_device(self, user, name, platform, ip=''):
        if user['source']!='session':raise DomainError('端末を登録するには、メールアドレスとパスワードまたはSSOでログインしてください')
        if user['tenantId'] and not self.login_settings(user['tenantId'])['allowDevices']:raise AccessError('このテナントでは生体認証による端末ログインが無効です')
        if platform not in PLATFORMS:raise DomainError('端末の種類が不正です')
        token=secrets.token_urlsafe(32);device=dict(id=str(uuid.uuid4()),userId=user['id'],name=self.name(name),platform=platform,tokenHash=token_hash(token),createdAt=now())
        self.repository.create_device(device)
        self.audit(user,'device.register',user['tenantId'],device['name'],{'platform':platform},ip)
        return {'deviceId':device['id'],'deviceToken':token}
    def device_login(self, device_id, token, ip='', user_agent=''):
        if not isinstance(device_id,str) or not isinstance(token,str):raise AuthError('端末ログインに失敗しました')
        user_id,state=self.repository.device_for_token(device_id,token_hash(token))
        user=self.repository.user(user_id) if user_id else None
        if state=='previous':
            # A rotated token came back: someone may hold a copy. The device must sign in again in full.
            self.repository.delete_devices(device_id=device_id)
            self.audit(user or {'id':'','name':''},'device.reuse_detected',user['tenantId'] if user else None,device_id,None,ip)
            raise AuthError('この端末の登録を安全のため解除しました。パスワードまたはSSOでログインし直してください')
        if state!='current' or not user or user['disabled']:raise AuthError('端末ログインに失敗しました。パスワードまたはSSOでログインし直してください')
        if user['tenantId'] and not self.login_settings(user['tenantId'])['allowDevices']:raise AuthError('このテナントでは生体認証による端末ログインが無効です')
        fresh=secrets.token_urlsafe(32)
        if not self.repository.rotate_device(device_id,token_hash(token),token_hash(fresh),ip):raise AuthError('端末ログインに失敗しました。もう一度お試しください')
        self.audit(user,'auth.login',user['tenantId'],user['email'],{'method':'device'},ip)
        return {**self.open_session(user,ip,user_agent,'device'),'deviceToken':fresh}
    def list_devices(self, user):
        return self.repository.devices(user_id=user['id'])
    def revoke_device(self, user, device_id, ip=''):
        device=next((d for d in self.repository.devices(user_id=user['id']) if d['id']==device_id),None)
        if not device:raise DomainError('端末が見つかりません')
        self.repository.delete_devices(device_id=device_id)
        self.audit(user,'device.revoke',user['tenantId'],device['name'],None,ip)
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
    def start_sso(self, email, ip='', client='web'):
        email=email.strip().lower() if isinstance(email,str) else ''
        user=self.repository.user(email=email) if email else None
        settings=self.login_settings(user['tenantId']) if user and user['tenantId'] else None
        if not user or user['disabled'] or not settings or not settings['allowSso'] or not settings['ssoIssuer']:
            self.audit({'id':user['id'] if user else '','name':user['name'] if user else email[:80]},'auth.sso_failed',user['tenantId'] if user else None,email[:254],{'reason':'not_available'},ip)
            raise AuthError('このメールアドレスではSSOを利用できません。パスワードでログインするか、テナント管理者に確認してください')
        state,nonce,verifier=sso.new_request()
        url=sso.authorization_url(settings['ssoIssuer'],settings['ssoClientId'],self.redirect_uri(),state,nonce,verifier,email)
        self.repository.save_sso_request(token_hash(state),user['id'],nonce,verifier,later(minutes=10),'app' if client=='app' else 'web')
        return {'url':url}
    def finish_sso(self, state, code, ip=''):
        """Returns where to send the browser: the page or the app, with a one-time code it exchanges for a
        session; the session token itself never appears in a URL."""
        request=self.repository.take_sso_request(token_hash(state)) if isinstance(state,str) and state else None
        if not request or not isinstance(code,str) or not code:raise AuthError('ログイン要求の有効期限が切れました。もう一度ログインしてください')
        try:return self.finish_sso_request(request,code,ip)
        except AuthError as error:
            # The app waits for its own address; errors go there too so it can show them.
            if request['client']=='app':return f'{self.app_scheme}://sso?error='+urllib_quote(str(error))
            raise
    def finish_sso_request(self, request, code, ip):
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
        self.audit(user,'auth.login',user['tenantId'],user['email'],{'method':'sso','client':request['client']},ip)
        return (f'{self.app_scheme}://sso?code=' if request['client']=='app' else '/#sso=')+urllib_quote(code)
    def exchange_code(self, code, ip='', user_agent=''):
        user_id=self.repository.take_login_code(token_hash(code)) if isinstance(code,str) and code else None
        user=self.repository.user(user_id) if user_id else None
        if not user or user['disabled']:raise AuthError('ログインの有効期限が切れました。もう一度ログインしてください')
        return self.open_session(user,ip,user_agent,'sso')
    def session_info(self, user):
        tenant=self.repository.tenant(user['tenantId']) if user['tenantId'] else None
        return {'user':user['name'],'email':user['email'],'role':user['role'],'source':user['source'],'mustChangePassword':user['mustChangePassword'] and user.get('method')=='password',
                'mfaEnabled':user.get('mfaEnabled',False),'mfaSetupRequired':user.get('mfaSetupRequired',False),'method':user.get('method','token'),
                'tenant':{'id':tenant['id'],'name':tenant['name']} if tenant else None,'workspace':tenant['workspace'] if tenant else None}

    # Permissions
    @staticmethod
    def require(user, *roles, message='この操作の権限がありません'):
        if user['role'] not in roles:raise AccessError(message)
    @staticmethod
    def require_ready(user):
        if user.get('mustChangePassword') and user.get('method')=='password':raise AccessError('初回ログインのため、パスワードを変更してください')
        if user.get('mfaSetupRequired'):raise AccessError('このアカウントでは多要素認証が必須です。認証アプリを設定してください')
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
    def system_overview(self, user, audit_scope='*'):
        """audit_scope: '*' for every event, 'system' for events outside tenants, or one tenant's ID."""
        self.require(user,SYSTEM_ADMIN)
        users,bottles,usage=self.repository.tenant_counts(month_start())
        tenants=self.repository.tenants()
        if audit_scope not in ('*','system') and not any(t['id']==audit_scope for t in tenants):audit_scope='*'
        return {'tenants':[dict(t,users=users.get(t['id'],0),bottles=bottles.get(t['id'],0),aiThisMonth=usage.get(t['id'],0)) for t in tenants],
                'systemAdmins':[self.public_user(u) for u in self.repository.users(system=True)]+[self.public_user(u) for u in self.env_users.values() if u['role']==SYSTEM_ADMIN],
                'audit':self.repository.audit_log(audit_scope,500),'auditScope':audit_scope,'self':user['id']}
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
        if action in ('resetSystemAdmin','deleteSystemAdmin','resetSystemAdminMfa'):
            target=self.repository.user(data.get('userId')) if isinstance(data.get('userId'),str) else None
            if not target or target['role']!=SYSTEM_ADMIN:raise DomainError('対象の全体管理者が見つかりません（環境変数の管理者は画面から変更できません）')
            if action=='deleteSystemAdmin':
                if target['id']==user['id']:raise DomainError('自分自身は削除できません')
                self.repository.delete_user(target['id']);self.audit(user,'system_admin.delete',None,target['email'],None,ip);return {}
            if action=='resetSystemAdminMfa':return self.reset_mfa(user,target,None,ip)
            return self.reset_password(user,target,None,ip)
        tenant=self.repository.tenant(data.get('tenantId')) if isinstance(data.get('tenantId'),str) else None
        if not tenant:raise DomainError('テナントが見つかりません')
        if action=='renameTenant':
            name=self.name(data.get('name'));self.repository.update_tenant(tenant['id'],name=name)
            self.audit(user,'tenant.rename',None,tenant['id'],{'from':tenant['name'],'to':name},ip);return {}
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
                'usage':self.usage(tenant),'devices':self.repository.devices(tenant_id=tenant),'audit':self.repository.audit_log(tenant),'self':user['id']}
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
            require_mfa,allow_devices=data.get('requireMfa') is True,data.get('allowDevices',True) is True
            settings=self.repository.tenant_settings(tenant);current=self.login_settings(tenant)
            issuer=data.get('ssoIssuer',current['ssoIssuer']);client_id=data.get('ssoClientId',current['ssoClientId']);secret=data.get('ssoClientSecret') or ''
            if issuer and not sso.https(issuer):raise DomainError('IDプロバイダーのURL（issuer）はhttps://で始まる必要があります')
            if not isinstance(client_id,str) or len(client_id)>300 or not isinstance(secret,str) or len(secret)>500:raise DomainError('クライアントIDまたはシークレットが不正です')
            if allow_sso and not (issuer and client_id and (secret or current['ssoSecretSet'])):raise DomainError('SSOを有効にするには、issuer・クライアントID・クライアントシークレットを設定してください')
            if allow_sso:self.redirect_uri()
            if not allow_password and not allow_sso:raise DomainError('パスワードとSSOの少なくとも一方を有効にしてください')
            if require_mfa and user['source']=='session' and not user['mfaEnabled']:raise DomainError('多要素認証を必須にする前に、ご自身の多要素認証を設定してください')
            if issuer and issuer.rstrip('/')!=current['ssoIssuer'].rstrip('/'):sso.discover(issuer)
            for key,value in (('auth_allow_password','1' if allow_password else '0'),('auth_allow_sso','1' if allow_sso else '0'),('mfa_required','1' if require_mfa else '0'),
                              ('allow_devices','1' if allow_devices else '0'),('sso_issuer',issuer.rstrip('/')),('sso_client_id',client_id)):
                settings.set_setting(key,value)
            if secret:settings.set_setting('sso_client_secret',secret)
            if not allow_devices:self.repository.delete_tenant_devices(tenant)
            self.audit(user,'settings.login',tenant,'',{'allowPassword':allow_password,'allowSso':allow_sso,'requireMfa':require_mfa,'allowDevices':allow_devices,
                                                        'ssoIssuer':issuer,'ssoClientId':client_id,'secretChanged':bool(secret)},ip)
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
        if action=='resetMfa':return self.reset_mfa(user,target,tenant,ip)
        if action=='revokeDevices':
            count=self.repository.delete_devices(user_id=target['id']);self.audit(user,'device.revoke',tenant,target['email'],{'devices':count},ip);return {}
        if action=='unlinkSso':
            self.repository.update_user(target['id'],ssoSubject=None);self.audit(user,'user.unlink_sso',tenant,target['email'],None,ip);return {}
        if action=='deleteUser':
            if target['id']==user['id']:raise DomainError('自分自身は削除できません')
            self.repository.delete_user(target['id']);self.audit(user,'user.delete',tenant,target['email'],None,ip);return {}
        raise DomainError('未対応の操作です')
    def reset_password(self, user, target, tenant, ip):
        password=temporary_password()
        self.repository.update_user(target['id'],passwordHash=hash_password(password),mustChangePassword=True,failedLogins=0,lockedUntil=None)
        # Account recovery: whoever held the old password or a registered phone is signed out.
        self.repository.delete_sessions(user_id=target['id']);self.repository.delete_devices(user_id=target['id'])
        self.audit(user,'user.password_reset',tenant,target['email'],None,ip)
        return {'user':self.public_user(target),'temporaryPassword':password}
    def reset_mfa(self, user, target, tenant, ip):
        """For a lost authenticator: the user sets MFA up again at the next password sign-in."""
        if target['id']==user['id']:raise DomainError('自分自身の多要素認証はリセットできません。回復コードを使ってください')
        self.clear_mfa(target['id']);self.repository.delete_sessions(user_id=target['id'])
        self.audit(user,'user.mfa_reset',tenant,target['email'],None,ip)
        return {}

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
        since=month_start()
        stored=self.repository.tenant_settings(tenant_id).get_setting('ai_monthly_limit')
        limit=int(stored) if stored else None
        return {'since':since,'used':self.repository.usage_count(tenant_id,since),'limit':limit,
                'summary':self.repository.usage_summary(tenant_id,since),'recent':self.repository.recent_usage(tenant_id)}
    def recognize(self, user, photo, ip=''):
        tenant=self.tenant_of(user);recognizer=self.recognizer(tenant)
        status=recognizer.status();usage=self.usage(tenant)
        if usage['limit'] is not None and usage['used']>=usage['limit']:
            self.repository.record_usage(tenant,user,status['label'],status['model'] or '','rejected')
            raise LimitError(f'今月のAI読み取り回数が上限（{usage["limit"]}回）に達しました。手入力で登録するか、テナント管理者に確認してください')
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
                'locked':bool(u.get('lockedUntil') and u['lockedUntil']>now()),'mustChangePassword':u.get('mustChangePassword',False),'lastLoginAt':u.get('lastLoginAt'),
                'mfaEnabled':u.get('mfaEnabled',False)}
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
