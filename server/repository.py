import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .domain import InventoryService, empty_state

DEFAULT_TENANT='default'

class SQLiteRepository:
    """Atomic command application per tenant; retry IDs are persisted with each tenant's state."""
    def __init__(self, path):
        self.path=path
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS tenants (id TEXT PRIMARY KEY, name TEXT NOT NULL, workspace TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, tenant_id TEXT REFERENCES tenants(id), name TEXT NOT NULL, role TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)')
            # Before tenants existed one shared inventory lived in `state`; it becomes the default tenant,
            # keeping its workspace ID so devices' cached and pending operations stay valid.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='state'").fetchone():
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT data FROM state WHERE id=1').fetchone()
                workspace=db.execute("SELECT value FROM metadata WHERE key='workspace'").fetchone()
                if row and not db.execute('SELECT 1 FROM tenants WHERE id=?',(DEFAULT_TENANT,)).fetchone():
                    db.execute('INSERT INTO tenants VALUES (?,?,?,?,?)',(DEFAULT_TENANT,'既定のテナント',workspace[0] if workspace else str(uuid.uuid4()),now(),row[0]))
                db.execute('DROP TABLE state')
                db.execute("DELETE FROM metadata WHERE key='workspace'")
    def connect(self):
        return sqlite3.connect(self.path,timeout=30)
    # Server settings live in metadata, outside the inventory state, so /api/state and exports never carry them.
    def get_setting(self, key):
        with self.connect() as db:
            row=db.execute('SELECT value FROM metadata WHERE key=?',('setting:'+key,)).fetchone()
            return row[0] if row else None
    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('setting:'+key,value))
    def delete_setting(self, key):
        with self.connect() as db:
            db.execute('DELETE FROM metadata WHERE key=?',('setting:'+key,))
    def tenants(self):
        with self.connect() as db:
            return [dict(id=r[0],name=r[1],workspace=r[2],createdAt=r[3]) for r in db.execute('SELECT id,name,workspace,created_at FROM tenants ORDER BY created_at,id')]
    def tenant(self, tenant_id):
        with self.connect() as db:
            r=db.execute('SELECT id,name,workspace,created_at FROM tenants WHERE id=?',(tenant_id,)).fetchone()
            return dict(id=r[0],name=r[1],workspace=r[2],createdAt=r[3]) if r else None
    def create_tenant(self, name, tenant_id=None):
        tenant=dict(id=tenant_id or str(uuid.uuid4()),name=name,workspace=str(uuid.uuid4()),createdAt=now())
        with self.connect() as db:
            db.execute('INSERT INTO tenants VALUES (?,?,?,?,?)',(tenant['id'],name,tenant['workspace'],tenant['createdAt'],json.dumps(empty_state())))
        return tenant
    def rename_tenant(self, tenant_id, name):
        with self.connect() as db:
            db.execute('UPDATE tenants SET name=? WHERE id=?',(name,tenant_id))
    def users(self):
        with self.connect() as db:
            return [dict(id=r[0],tenantId=r[1],name=r[2],role=r[3],createdAt=r[4]) for r in db.execute('SELECT id,tenant_id,name,role,created_at FROM users ORDER BY created_at,id')]
    def user_by_token_hash(self, token_hash):
        with self.connect() as db:
            r=db.execute('SELECT id,tenant_id,name,role FROM users WHERE token_hash=?',(token_hash,)).fetchone()
            return dict(id=r[0],tenantId=r[1],name=r[2],role=r[3]) if r else None
    def create_user(self, tenant_id, name, role, token_hash):
        user=dict(id=str(uuid.uuid4()),tenantId=tenant_id,name=name,role=role,createdAt=now())
        with self.connect() as db:
            db.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',(user['id'],tenant_id,name,role,token_hash,user['createdAt']))
        return user
    def delete_user(self, user_id):
        with self.connect() as db:
            db.execute('DELETE FROM users WHERE id=?',(user_id,))
    def set_token_hash(self, user_id, token_hash):
        with self.connect() as db:
            db.execute('UPDATE users SET token_hash=? WHERE id=?',(token_hash,user_id))
    def read(self, tenant_id):
        with self.connect() as db:
            return json.loads(db.execute('SELECT data FROM tenants WHERE id=?',(tenant_id,)).fetchone()[0])
    def execute(self, tenant_id, command):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state=json.loads(db.execute('SELECT data FROM tenants WHERE id=?',(tenant_id,)).fetchone()[0])
            result=InventoryService().apply(state,command)
            db.execute('UPDATE tenants SET data=? WHERE id=?',(json.dumps(result,ensure_ascii=False),tenant_id))
            return result

def now():
    return datetime.now(timezone.utc).isoformat()
