import concurrent.futures
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
from server.app import create_server
from server.repository import SQLiteRepository
from server.domain import DomainError

class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.repo=SQLiteRepository(self.tmp.name+'/data.db');self.n=0
        self.repo.execute(self.cmd('addFridge',{'id':'f','name':'冷蔵庫'}))
    def tearDown(self):self.tmp.cleanup()
    def cmd(self,kind,payload):
        self.n+=1;return dict(id=str(self.n),type=kind,payload=payload,actor='テスト')
    def receive(self,volume):return self.repo.execute(self.cmd('receive',dict(name='日本酒',volume=volume,count=1,ids=['b'],fridgeId='f')))
    def test_consumption(self):
        self.receive(720)
        for version in range(1,5):self.repo.execute(self.cmd('consume',dict(bottleId='b',version=version)))
        self.assertEqual(self.repo.read()['bottles'][0]['remaining'],0)
        with self.assertRaises(DomainError):self.repo.execute(self.cmd('consume',dict(bottleId='b',version=5)))
    def test_bottle(self):
        self.receive(300);self.repo.execute(self.cmd('consume',dict(bottleId='b',version=1)))
        self.assertEqual(self.repo.read()['bottles'][0]['remaining'],0)
    def test_concurrency(self):
        self.receive(720)
        commands=[self.cmd('consume',dict(bottleId='b',version=1)) for _ in range(2)]
        def apply(c):
            try:self.repo.execute(c);return True
            except DomainError:return False
        with concurrent.futures.ThreadPoolExecutor() as pool:results=list(pool.map(apply,commands))
        self.assertEqual(sorted(results),[False,True]);self.assertEqual(self.repo.read()['bottles'][0]['remaining'],540)
    def test_retry_undo(self):
        self.receive(1800);c=self.cmd('consume',dict(bottleId='b',version=1));self.repo.execute(c);self.repo.execute(c)
        self.assertEqual(self.repo.read()['bottles'][0]['remaining'],1620)
        self.repo.execute(self.cmd('undo',dict(bottleId='b',version=2,eventId=c['id'])))
        self.assertEqual(self.repo.read()['bottles'][0]['remaining'],1800)
    def test_restart(self):
        self.receive(720);self.assertEqual(SQLiteRepository(self.repo.path).read(),self.repo.read())

class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.token='test-token-longer-than-24-characters'
        self.env=patch.dict(os.environ,{'DATABASE_PATH':self.tmp.name+'/db','BACCHUS_USERS':json.dumps({self.token:'Alice'})});self.env.start()
        self.http=create_server(port=0);self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start();self.url=f'http://127.0.0.1:{self.http.server_port}'
    def tearDown(self):self.http.shutdown();self.http.server_close();self.env.stop();self.tmp.cleanup()
    def test_auth_and_static_boundary(self):
        with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(self.url+'/api/state')
        self.assertEqual(e.exception.code,401)
        with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(self.url+'/server/app.py')
        self.assertEqual(e.exception.code,404)
        with urllib.request.urlopen(self.url+'/') as response:self.assertIn(b'BACCHUS',response.read())
    def test_actor_and_retry(self):
        c=dict(id='once',type='addFridge',actor='FORGED',payload=dict(id='f',name='テスト'))
        def send():
            request=urllib.request.Request(self.url+'/api/commands',data=json.dumps(c).encode(),headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
            with urllib.request.urlopen(request) as response:return json.load(response)
        s=send();self.assertEqual(s['events'][0]['actor'],'Alice');self.assertEqual(send(),s)

if __name__=='__main__':unittest.main()
