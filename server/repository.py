"""SQLite storage. Tables are normalized per tenant so they can move to a server database later;
every write runs in one transaction with foreign keys enforced."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .domain import InventoryService, empty_state

DEFAULT_TENANT='default'
SCHEMA_VERSION=2
SCHEMA='''
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tenants (id TEXT PRIMARY KEY, name TEXT NOT NULL, workspace TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, ai_monthly_cap INTEGER);
CREATE TABLE IF NOT EXISTS tenant_settings (tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (tenant_id,key));
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, tenant_id TEXT REFERENCES tenants(id) ON DELETE CASCADE, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('system_admin','tenant_admin','member')), password_hash TEXT, must_change_password INTEGER NOT NULL DEFAULT 0,
  sso_subject TEXT, disabled INTEGER NOT NULL DEFAULT 0, failed_logins INTEGER NOT NULL DEFAULT 0, locked_until TEXT,
  created_at TEXT NOT NULL, last_login_at TEXT, CHECK ((role='system_admin')=(tenant_id IS NULL)));
CREATE INDEX IF NOT EXISTS users_tenant ON users(tenant_id);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, expires_at TEXT NOT NULL, ip TEXT NOT NULL, user_agent TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS sso_requests (state_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, nonce TEXT NOT NULL, verifier TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_codes (code_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS refrigerators (tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, id TEXT NOT NULL, name TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY (tenant_id,id));
CREATE TABLE IF NOT EXISTS bottles (
  tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, id TEXT NOT NULL, position INTEGER NOT NULL, name TEXT NOT NULL, brewery TEXT NOT NULL,
  category TEXT NOT NULL, production_date TEXT NOT NULL, volume INTEGER NOT NULL, remaining INTEGER NOT NULL CHECK (remaining BETWEEN 0 AND volume),
  fridge_id TEXT NOT NULL, shelf TEXT NOT NULL, received_at TEXT NOT NULL, opened_at TEXT, version INTEGER NOT NULL, import_origin TEXT, PRIMARY KEY (tenant_id,id));
CREATE INDEX IF NOT EXISTS bottles_fridge ON bottles(tenant_id,fridge_id);
CREATE TABLE IF NOT EXISTS bottle_photos (tenant_id TEXT NOT NULL, bottle_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY (tenant_id,bottle_id), FOREIGN KEY (tenant_id,bottle_id) REFERENCES bottles(tenant_id,id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS events (tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, seq INTEGER NOT NULL, id TEXT NOT NULL, type TEXT NOT NULL, at TEXT NOT NULL, actor TEXT NOT NULL, bottle_id TEXT, data TEXT NOT NULL, PRIMARY KEY (tenant_id,id), UNIQUE (tenant_id,seq));
CREATE TABLE IF NOT EXISTS ai_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, user_id TEXT NOT NULL, user_name TEXT NOT NULL, at TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ai_usage_tenant_at ON ai_usage(tenant_id,at);
CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, tenant_id TEXT, actor_id TEXT NOT NULL, actor_name TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, detail TEXT NOT NULL, ip TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS audit_tenant ON audit_log(tenant_id,id);
'''
BOTTLE_COLUMNS=(('id','id'),('name','name'),('brewery','brewery'),('category','category'),('productionDate','production_date'),('volume','volume'),('remaining','remaining'),
                ('fridgeId','fridge_id'),('shelf','shelf'),('receivedAt','received_at'),('openedAt','opened_at'),('version','version'),('importOrigin','import_origin'))
USER_COLUMNS='id,tenant_id,email,name,role,password_hash,must_change_password,sso_subject,disabled,failed_logins,locked_until,created_at,last_login_at'

def now():
    return datetime.now(timezone.utc).isoformat()

def user_row(r):
    keys=('id','tenantId','email','name','role','passwordHash','mustChangePassword','ssoSubject','disabled','failedLogins','lockedUntil','createdAt','lastLoginAt')
    user=dict(zip(keys,r))
    for key in ('mustChangePassword','disabled'):user[key]=bool(user[key])
    return user

def without_photo(bottle):
    return {k:v for k,v in bottle.items() if k!='photo'} if isinstance(bottle,dict) else bottle

class SQLiteRepository:
    def __init__(self, path):
        self.path=path
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(path,timeout=30,isolation_level=None)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('BEGIN IMMEDIATE')
            self.upgrade(db)
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK');raise
        finally:db.close()
    @contextmanager
    def transaction(self, write=False):
        db=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK');raise
        finally:db.close()
    @staticmethod
    def table_columns(db, table):
        return [r[1] for r in db.execute(f'PRAGMA table_info({table})')]
    def upgrade(self, db):
        """Moves either earlier format into the normalized schema in the same transaction:
        v0 kept one shared inventory in `state`, v1 kept one JSON document per tenant."""
        version=db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone() if self.table_columns(db,'metadata') else None
        if version and int(version[0])>=SCHEMA_VERSION:return
        legacy=[]
        if self.table_columns(db,'state'):
            row=db.execute('SELECT data FROM state WHERE id=1').fetchone()
            workspace=db.execute("SELECT value FROM metadata WHERE key='workspace'").fetchone()
            if row:legacy.append(dict(id=DEFAULT_TENANT,name='既定のテナント',workspace=workspace[0] if workspace else str(uuid.uuid4()),created_at=now(),data=row[0]))
            db.execute('DROP TABLE state')
        if 'data' in self.table_columns(db,'tenants'):
            legacy+=[dict(zip(('id','name','workspace','created_at','data'),r)) for r in db.execute('SELECT id,name,workspace,created_at,data FROM tenants')]
            db.execute('DROP TABLE tenants')
        if self.table_columns(db,'users') and 'email' not in self.table_columns(db,'users'):
            # v1 accounts held only screen-issued tokens; they cannot sign in by password, so tenant administrators re-create them.
            db.execute('DROP TABLE users')
        # executescript() would commit first; single statements keep the upgrade in one transaction.
        for statement in SCHEMA.split(';'):
            if statement.strip():db.execute(statement)
        for t in legacy:
            db.execute('INSERT INTO tenants (id,name,workspace,created_at) VALUES (?,?,?,?)',(t['id'],t['name'],t['workspace'],t['created_at']))
            self.write_state(db,t['id'],empty_state(),json.loads(t['data']))
        # AI settings used to be server-wide; they now belong to the tenant that inherited the shared inventory.
        settings=db.execute("SELECT key,value FROM metadata WHERE key LIKE 'setting:%'").fetchall()
        if settings and db.execute('SELECT 1 FROM tenants WHERE id=?',(DEFAULT_TENANT,)).fetchone():
            db.executemany('INSERT OR REPLACE INTO tenant_settings VALUES (?,?,?)',[(DEFAULT_TENANT,k.removeprefix('setting:'),v) for k,v in settings])
        db.execute("DELETE FROM metadata WHERE key LIKE 'setting:%' OR key='workspace'")
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('schema_version',?)",(str(SCHEMA_VERSION),))

    # Tenants
    def tenants(self):
        with self.transaction() as db:
            return [self.tenant_row(r) for r in db.execute('SELECT id,name,workspace,created_at,ai_monthly_cap FROM tenants ORDER BY created_at,id')]
    def tenant(self, tenant_id):
        with self.transaction() as db:
            r=db.execute('SELECT id,name,workspace,created_at,ai_monthly_cap FROM tenants WHERE id=?',(tenant_id,)).fetchone()
            return self.tenant_row(r) if r else None
    @staticmethod
    def tenant_row(r):
        return dict(id=r[0],name=r[1],workspace=r[2],createdAt=r[3],aiMonthlyCap=r[4])
    def create_tenant(self, name, tenant_id=None, admin=None):
        """Creates a tenant, optionally with its first administrator (dict for create_user), atomically."""
        tenant=dict(id=tenant_id or str(uuid.uuid4()),name=name,workspace=str(uuid.uuid4()),createdAt=now(),aiMonthlyCap=None)
        with self.transaction(True) as db:
            db.execute('INSERT INTO tenants (id,name,workspace,created_at) VALUES (?,?,?,?)',(tenant['id'],name,tenant['workspace'],tenant['createdAt']))
            if admin:self.insert_user(db,dict(admin,tenantId=tenant['id']))
        return tenant
    def update_tenant(self, tenant_id, **fields):
        columns={'name':'name','aiMonthlyCap':'ai_monthly_cap'}
        with self.transaction(True) as db:
            for key,value in fields.items():db.execute(f'UPDATE tenants SET {columns[key]}=? WHERE id=?',(value,tenant_id))
    def delete_tenant(self, tenant_id):
        """Removes the tenant and, by cascade, its users, sessions, settings, inventory, photos and AI usage."""
        with self.transaction(True) as db:
            db.execute('DELETE FROM tenants WHERE id=?',(tenant_id,))
            db.execute('DELETE FROM audit_log WHERE tenant_id=?',(tenant_id,))
    def tenant_counts(self, since):
        with self.transaction() as db:
            users=dict(db.execute('SELECT tenant_id,COUNT(*) FROM users WHERE tenant_id IS NOT NULL GROUP BY tenant_id').fetchall())
            bottles=dict(db.execute('SELECT tenant_id,COUNT(*) FROM bottles WHERE remaining>0 GROUP BY tenant_id').fetchall())
            usage=dict(db.execute("SELECT tenant_id,COUNT(*) FROM ai_usage WHERE at>=? AND status!='rejected' GROUP BY tenant_id",(since,)).fetchall())
            return users,bottles,usage

    # Settings: server-wide in metadata, per tenant in tenant_settings.
    def get_setting(self, key):
        with self.transaction() as db:
            row=db.execute('SELECT value FROM metadata WHERE key=?',('setting:'+key,)).fetchone()
            return row[0] if row else None
    def set_setting(self, key, value):
        with self.transaction(True) as db:db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('setting:'+key,value))
    def delete_setting(self, key):
        with self.transaction(True) as db:db.execute('DELETE FROM metadata WHERE key=?',('setting:'+key,))
    def tenant_settings(self, tenant_id):
        return TenantSettings(self,tenant_id)

    # Users and sign-in
    def users(self, tenant_id=None, system=False):
        with self.transaction() as db:
            where,args=('tenant_id IS NULL',()) if system else ('tenant_id=?',(tenant_id,))
            return [user_row(r) for r in db.execute(f'SELECT {USER_COLUMNS} FROM users WHERE {where} ORDER BY created_at,id',args)]
    def user(self, user_id=None, email=None):
        with self.transaction() as db:
            r=db.execute(f'SELECT {USER_COLUMNS} FROM users WHERE '+('id=?' if user_id else 'email=?'),(user_id or email,)).fetchone()
            return user_row(r) if r else None
    @staticmethod
    def insert_user(db, user):
        db.execute('INSERT INTO users (id,tenant_id,email,name,role,password_hash,must_change_password,created_at) VALUES (?,?,?,?,?,?,?,?)',
                   (user['id'],user['tenantId'],user['email'],user['name'],user['role'],user.get('passwordHash'),int(user.get('mustChangePassword',False)),user['createdAt']))
    def create_user(self, user):
        with self.transaction(True) as db:self.insert_user(db,user)
        return user
    def update_user(self, user_id, **fields):
        columns={'name':'name','email':'email','role':'role','passwordHash':'password_hash','mustChangePassword':'must_change_password','ssoSubject':'sso_subject',
                 'disabled':'disabled','failedLogins':'failed_logins','lockedUntil':'locked_until','lastLoginAt':'last_login_at'}
        with self.transaction(True) as db:
            for key,value in fields.items():db.execute(f'UPDATE users SET {columns[key]}=? WHERE id=?',(int(value) if isinstance(value,bool) else value,user_id))
    def delete_user(self, user_id):
        with self.transaction(True) as db:db.execute('DELETE FROM users WHERE id=?',(user_id,))
    def create_session(self, token_hash, user_id, expires_at, ip, user_agent):
        at=now()
        with self.transaction(True) as db:
            db.execute('DELETE FROM sessions WHERE expires_at<?',(at,))
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?)',(token_hash,user_id,at,at,expires_at,ip,user_agent[:300]))
    def session(self, token_hash):
        with self.transaction() as db:
            r=db.execute('SELECT user_id,created_at,last_seen_at,expires_at FROM sessions WHERE token_hash=?',(token_hash,)).fetchone()
            return dict(userId=r[0],createdAt=r[1],lastSeenAt=r[2],expiresAt=r[3]) if r else None
    def touch_session(self, token_hash, last_seen_at, expires_at):
        with self.transaction(True) as db:db.execute('UPDATE sessions SET last_seen_at=?,expires_at=? WHERE token_hash=?',(last_seen_at,expires_at,token_hash))
    def delete_sessions(self, token_hash=None, user_id=None, keep=''):
        with self.transaction(True) as db:
            if token_hash:db.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash,))
            else:db.execute('DELETE FROM sessions WHERE user_id=? AND token_hash!=?',(user_id,keep))
    def save_sso_request(self, state_hash, user_id, nonce, verifier, expires_at):
        with self.transaction(True) as db:
            db.execute('DELETE FROM sso_requests WHERE expires_at<?',(now(),))
            db.execute('INSERT INTO sso_requests VALUES (?,?,?,?,?)',(state_hash,user_id,nonce,verifier,expires_at))
    def take_sso_request(self, state_hash):
        """One use only: the request is removed as it is read."""
        with self.transaction(True) as db:
            r=db.execute('SELECT user_id,nonce,verifier,expires_at FROM sso_requests WHERE state_hash=?',(state_hash,)).fetchone()
            db.execute('DELETE FROM sso_requests WHERE state_hash=?',(state_hash,))
            return dict(userId=r[0],nonce=r[1],verifier=r[2],expiresAt=r[3]) if r and r[3]>now() else None
    def save_login_code(self, code_hash, user_id, expires_at):
        with self.transaction(True) as db:
            db.execute('DELETE FROM login_codes WHERE expires_at<?',(now(),))
            db.execute('INSERT INTO login_codes VALUES (?,?,?)',(code_hash,user_id,expires_at))
    def take_login_code(self, code_hash):
        with self.transaction(True) as db:
            r=db.execute('SELECT user_id,expires_at FROM login_codes WHERE code_hash=?',(code_hash,)).fetchone()
            db.execute('DELETE FROM login_codes WHERE code_hash=?',(code_hash,))
            return r[0] if r and r[1]>now() else None

    # Inventory: the domain works on a state document; it is assembled from and written back to rows.
    def load_state(self, db, tenant_id, photos=False):
        state=empty_state()
        state['refrigerators']=[dict(id=r[0],name=r[1]) for r in db.execute('SELECT id,name FROM refrigerators WHERE tenant_id=? ORDER BY position',(tenant_id,))]
        names=[c for _,c in BOTTLE_COLUMNS]
        for r in db.execute(f'SELECT {",".join(names)} FROM bottles WHERE tenant_id=? ORDER BY position',(tenant_id,)):
            bottle={k:v for (k,_),v in zip(BOTTLE_COLUMNS,r) if k!='importOrigin' or v is not None}
            state['bottles'].append(bottle)
        if photos:
            stored=dict(db.execute('SELECT bottle_id,data FROM bottle_photos WHERE tenant_id=?',(tenant_id,)).fetchall())
            for b in state['bottles']:b['photo']=stored.get(b['id'],'')
        state['events']=[dict(id=r[0],type=r[1],at=r[2],actor=r[3],**json.loads(r[4])) for r in db.execute('SELECT id,type,at,actor,data FROM events WHERE tenant_id=? ORDER BY seq',(tenant_id,))]
        return state
    def write_state(self, db, tenant_id, old, new):
        """Writes only what changed; events are append-only."""
        old_fridges={f['id']:f for f in old['refrigerators']}
        for position,f in enumerate(new['refrigerators']):
            if f['id'] not in old_fridges:db.execute('INSERT INTO refrigerators VALUES (?,?,?,?)',(tenant_id,f['id'],f['name'],position))
            else:db.execute('UPDATE refrigerators SET name=?,position=? WHERE tenant_id=? AND id=?',(f['name'],position,tenant_id,f['id']))
        for fid in old_fridges.keys()-{f['id'] for f in new['refrigerators']}:db.execute('DELETE FROM refrigerators WHERE tenant_id=? AND id=?',(tenant_id,fid))
        old_bottles={b['id']:b for b in old['bottles']}
        for position,b in enumerate(new['bottles']):
            values=[b.get(k) for k,_ in BOTTLE_COLUMNS]
            if b['id'] not in old_bottles:
                db.execute(f'INSERT INTO bottles (tenant_id,position,{",".join(c for _,c in BOTTLE_COLUMNS)}) VALUES (?,?{",?"*len(BOTTLE_COLUMNS)})',(tenant_id,position,*values))
                if b.get('photo'):db.execute('INSERT INTO bottle_photos VALUES (?,?,?)',(tenant_id,b['id'],b['photo']))
            elif without_photo(b)!=old_bottles[b['id']]:
                db.execute(f'UPDATE bottles SET position=?,{",".join(c+"=?" for _,c in BOTTLE_COLUMNS[1:])} WHERE tenant_id=? AND id=?',(position,*values[1:],tenant_id,b['id']))
        if new['events'][:len(old['events'])]!=old['events']:raise ValueError('history is append-only')
        for seq,e in enumerate(new['events'][len(old['events']):],start=len(old['events'])):
            # A bottle's photo is stored once; history keeps the other fields.
            data={k:(without_photo(v) if k in ('before','after') else v) for k,v in e.items() if k not in ('id','type','at','actor')}
            if isinstance(data.get('command'),dict) and isinstance(data['command'].get('payload'),dict) and 'photo' in data['command']['payload']:
                data['command']=dict(data['command'],payload=without_photo(data['command']['payload']))
            db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?)',(tenant_id,seq,e['id'],e['type'],e['at'],e['actor'],e.get('bottleId'),json.dumps(data,ensure_ascii=False)))
    def read(self, tenant_id):
        with self.transaction() as db:
            return self.load_state(db,tenant_id,photos=True)
    def execute(self, tenant_id, command):
        with self.transaction(True) as db:
            if not db.execute('SELECT 1 FROM tenants WHERE id=?',(tenant_id,)).fetchone():raise LookupError('tenant')
            state=self.load_state(db,tenant_id)
            result=InventoryService().apply(state,command)
            self.write_state(db,tenant_id,state,result)
            return self.load_state(db,tenant_id,photos=True)

    # AI usage and audit trail
    def record_usage(self, tenant_id, user, provider, model, status, input_tokens=0, output_tokens=0):
        with self.transaction(True) as db:
            db.execute('INSERT INTO ai_usage (tenant_id,user_id,user_name,at,provider,model,status,input_tokens,output_tokens) VALUES (?,?,?,?,?,?,?,?,?)',
                       (tenant_id,user['id'],user['name'],now(),provider,model,status,int(input_tokens or 0),int(output_tokens or 0)))
    def usage_count(self, tenant_id, since):
        with self.transaction() as db:
            return db.execute("SELECT COUNT(*) FROM ai_usage WHERE tenant_id=? AND at>=? AND status!='rejected'",(tenant_id,since)).fetchone()[0]
    def usage_summary(self, tenant_id, since):
        with self.transaction() as db:
            rows=db.execute('SELECT provider,model,status,COUNT(*),SUM(input_tokens),SUM(output_tokens) FROM ai_usage WHERE tenant_id=? AND at>=? GROUP BY provider,model,status ORDER BY provider,model,status',(tenant_id,since))
            return [dict(provider=r[0],model=r[1],status=r[2],count=r[3],inputTokens=r[4],outputTokens=r[5]) for r in rows]
    def recent_usage(self, tenant_id, limit=50):
        with self.transaction() as db:
            rows=db.execute('SELECT at,user_name,provider,model,status,input_tokens,output_tokens FROM ai_usage WHERE tenant_id=? ORDER BY id DESC LIMIT ?',(tenant_id,limit))
            return [dict(at=r[0],user=r[1],provider=r[2],model=r[3],status=r[4],inputTokens=r[5],outputTokens=r[6]) for r in rows]
    def audit(self, tenant_id, actor, action, target='', detail=None, ip=''):
        with self.transaction(True) as db:
            db.execute('INSERT INTO audit_log (at,tenant_id,actor_id,actor_name,action,target,detail,ip) VALUES (?,?,?,?,?,?,?,?)',
                       (now(),tenant_id,actor.get('id',''),actor.get('name',''),action,target,json.dumps(detail or {},ensure_ascii=False),ip))
    def audit_log(self, tenant_id, limit=200):
        with self.transaction() as db:
            rows=db.execute('SELECT at,actor_name,action,target,detail,ip FROM audit_log WHERE '+('tenant_id IS NULL' if tenant_id is None else 'tenant_id=?')+' ORDER BY id DESC LIMIT ?',
                            (limit,) if tenant_id is None else (tenant_id,limit))
            return [dict(at=r[0],actor=r[1],action=r[2],target=r[3],detail=json.loads(r[4]),ip=r[5]) for r in rows]

class TenantSettings:
    """The settings interface the AI recognizer uses, scoped to one tenant."""
    def __init__(self, repository, tenant_id):
        self.repository,self.tenant_id=repository,tenant_id
    def get_setting(self, key):
        with self.repository.transaction() as db:
            row=db.execute('SELECT value FROM tenant_settings WHERE tenant_id=? AND key=?',(self.tenant_id,key)).fetchone()
            return row[0] if row else None
    def set_setting(self, key, value):
        with self.repository.transaction(True) as db:db.execute('INSERT OR REPLACE INTO tenant_settings VALUES (?,?,?)',(self.tenant_id,key,value))
    def delete_setting(self, key):
        with self.repository.transaction(True) as db:db.execute('DELETE FROM tenant_settings WHERE tenant_id=? AND key=?',(self.tenant_id,key))
