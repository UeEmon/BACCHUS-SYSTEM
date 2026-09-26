import json
import sqlite3
import uuid
from pathlib import Path
from .domain import InventoryService, empty_state

class SQLiteRepository:
    """Atomic command application; retry IDs are persisted with the state."""
    def __init__(self, path):
        self.path=path
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)',('workspace',str(uuid.uuid4())))
            db.execute('INSERT OR IGNORE INTO state VALUES (1,?)',(json.dumps(empty_state()),))
    def connect(self):
        return sqlite3.connect(self.path,timeout=30)
    def workspace(self):
        with self.connect() as db:
            return db.execute("SELECT value FROM metadata WHERE key='workspace'").fetchone()[0]
    def read(self):
        with self.connect() as db:
            return json.loads(db.execute('SELECT data FROM state WHERE id=1').fetchone()[0])
    def execute(self, command):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state=json.loads(db.execute('SELECT data FROM state WHERE id=1').fetchone()[0])
            result=InventoryService().apply(state,command)
            db.execute('UPDATE state SET data=? WHERE id=1',(json.dumps(result,ensure_ascii=False),))
            return result
