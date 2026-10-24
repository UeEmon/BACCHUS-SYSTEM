"""Tenants, users and what each role may do. Tokens are issued here, shown once and stored only as hashes."""
import hashlib
import hmac
import json
import secrets
from .domain import DomainError, string
from .repository import DEFAULT_TENANT

SYSTEM_ADMIN,TENANT_ADMIN,MEMBER='system_admin','tenant_admin','member'
ROLES=(SYSTEM_ADMIN,TENANT_ADMIN,MEMBER)
# Fridge layout and bulk imports belong to tenant administrators; members use every fridge's bottles.
TENANT_ADMIN_COMMANDS={'addFridge','renameFridge','deleteFridge','importPersonal'}

class AccessError(PermissionError):
    pass

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()

def parse_env_users(text):
    """BACCHUS_USERS bootstraps accounts that cannot be edited on screen. A token maps to a display
    name or to {"name", "role"}. Roles: system_admin (no tenant), tenant_admin and member (both in the
    default tenant). A bare name and the former "admin" role are tenant administrators; a missing role is a member."""
    users=json.loads(text)
    error=ValueError('BACCHUS_USERS requires JSON {"long-random-token": "display name" or {"name": "display name", "role": "system_admin", "tenant_admin" or "member"}}; minimum token length 24')
    if not isinstance(users,dict):raise error
    parsed={}
    for key,value in users.items():
        if isinstance(value,str):value={'name':value,'role':TENANT_ADMIN}
        if not isinstance(key,str) or len(key)<24 or not isinstance(value,dict) or set(value)-{'name','role'} or not isinstance(value.get('name'),str) or not value['name']:raise error
        role={'admin':TENANT_ADMIN}.get(value.get('role',MEMBER),value.get('role',MEMBER))
        if role not in ROLES:raise error
        parsed[key]=dict(id='env:'+token_hash(key)[:12],name=value['name'],role=role,tenantId=None if role==SYSTEM_ADMIN else DEFAULT_TENANT,source='env')
    return parsed

class AccountService:
    def __init__(self, repository, env_users=None):
        self.repository=repository
        self.env_users=env_users or {}
        if any(u['tenantId'] for u in self.env_users.values()) and not repository.tenant(DEFAULT_TENANT):
            repository.create_tenant('既定のテナント',DEFAULT_TENANT)
    def has_system_admin(self):
        return any(u['role']==SYSTEM_ADMIN for u in [*self.env_users.values(),*self.repository.users()])
    def authenticate(self, header):
        """The token holder as {id, name, role, tenantId, source}, or None."""
        token=header.removeprefix('Bearer ')
        if not token:return None
        user=next((dict(u) for key,u in self.env_users.items() if hmac.compare_digest(key,token)),None)
        if user:return user
        user=self.repository.user_by_token_hash(token_hash(token))
        return dict(user,source='db') if user else None
    def session(self, user):
        tenant=self.repository.tenant(user['tenantId']) if user['tenantId'] else None
        return {'user':user['name'],'role':user['role'],'tenant':{'id':tenant['id'],'name':tenant['name']} if tenant else None,'workspace':tenant['workspace'] if tenant else None}
    @staticmethod
    def tenant_of(user):
        if not user['tenantId']:raise AccessError('全体管理者はテナントの在庫を操作できません。テナントの利用者のトークンを使用してください')
        return user['tenantId']
    @staticmethod
    def check_command(user, command):
        if command.get('type') in TENANT_ADMIN_COMMANDS and user['role']!=TENANT_ADMIN:
            raise AccessError('冷蔵庫の管理と在庫の一括追加はテナント管理者のみ可能です')
    @staticmethod
    def require_system_admin(user, message='この操作は全体管理者のみ可能です'):
        if user['role']!=SYSTEM_ADMIN:raise AccessError(message)
    def overview(self, user):
        """Tenants and users the caller administers. Tokens are never listed."""
        if user['role']==MEMBER:raise AccessError('利用者の管理は管理者のみ可能です')
        everyone=[dict(u,source='db') for u in self.repository.users()]+[{k:v for k,v in u.items()} for u in self.env_users.values()]
        tenants=self.repository.tenants() if user['role']==SYSTEM_ADMIN else [self.repository.tenant(user['tenantId'])]
        result={'role':user['role'],'self':user['id'],'tenants':[{'id':t['id'],'name':t['name'],'users':[u for u in everyone if u['tenantId']==t['id']]} for t in tenants]}
        if user['role']==SYSTEM_ADMIN:result['systemAdmins']=[u for u in everyone if u['role']==SYSTEM_ADMIN]
        return result
    def act(self, user, data):
        action=data.get('action')
        if user['role']==MEMBER:raise AccessError('利用者の管理は管理者のみ可能です')
        if action=='createTenant':
            self.require_system_admin(user)
            tenant=self.repository.create_tenant(self.name(data.get('name')))
            return {'tenant':{'id':tenant['id'],'name':tenant['name']}}
        if action=='renameTenant':
            self.require_system_admin(user)
            self.existing_tenant(data.get('tenantId'));self.repository.rename_tenant(data['tenantId'],self.name(data.get('name')))
            return {}
        if action=='createUser':
            role=data.get('role')
            if role not in ROLES:raise DomainError('ロールが不正です')
            tenant_id=None if role==SYSTEM_ADMIN else data.get('tenantId')
            if role==SYSTEM_ADMIN:self.require_system_admin(user,'全体管理者を追加できるのは全体管理者のみです')
            else:self.administers(user,self.existing_tenant(tenant_id))
            token=secrets.token_urlsafe(32)
            created=self.repository.create_user(tenant_id,self.name(data.get('name')),role,token_hash(token))
            return {'user':created,'token':token}
        if action in ('deleteUser','reissueToken'):
            target=next((u for u in self.repository.users() if u['id']==data.get('userId')),None)
            if target is None:raise DomainError('対象の利用者が見つかりません（環境変数で設定した利用者は画面から変更できません）')
            if target['role']==SYSTEM_ADMIN:self.require_system_admin(user)
            else:self.administers(user,target['tenantId'])
            if action=='deleteUser':
                if target['id']==user['id']:raise DomainError('自分自身は削除できません')
                self.repository.delete_user(target['id']);return {}
            token=secrets.token_urlsafe(32)
            self.repository.set_token_hash(target['id'],token_hash(token))
            return {'user':target,'token':token}
        raise DomainError('未対応の操作です')
    def existing_tenant(self, tenant_id):
        if not isinstance(tenant_id,str) or not self.repository.tenant(tenant_id):raise DomainError('テナントが見つかりません')
        return tenant_id
    @staticmethod
    def administers(user, tenant_id):
        if user['role']==SYSTEM_ADMIN:return
        if user['role']!=TENANT_ADMIN or user['tenantId']!=tenant_id:raise AccessError('他のテナントの利用者は管理できません')
    @staticmethod
    def name(value):
        result=string(value if isinstance(value,str) else '',80)
        if not result:raise DomainError('名前を入力してください')
        return result
