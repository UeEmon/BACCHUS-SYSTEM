import tempfile
import unittest
from server.repository import SQLiteRepository
from server.domain import DomainError

class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.repo=SQLiteRepository(self.tmp.name+'/db')
        self.source=dict(schema=1,refrigerators=[dict(id='f',name='自宅')],bottles=[dict(id='b',name='酒',volume=720,remaining=540,version=2,fridgeId='f',receivedAt='2026-09-25T00:00:00Z',openedAt='2026-09-26T00:00:00Z')])
    def tearDown(self):self.tmp.cleanup()
    def command(self,cid='m'):
        return dict(id=cid,type='importPersonal',actor='Alice',payload=dict(snapshot=self.source))
    def test_transfer_retry_and_existing_shared_changes(self):
        s=self.repo.execute(self.command());self.assertEqual(s['bottles'][0]['remaining'],540)
        self.repo.execute(dict(id='drink',type='consume',actor='Bob',payload=dict(bottleId='b',version=1)))
        self.repo.execute(self.command());s=self.repo.execute(self.command('m2'))
        self.assertEqual(s['bottles'][0]['remaining'],360);self.assertEqual(len(s['bottles']),1)
        self.assertEqual(s['events'][-1]['skipped'],1);self.assertEqual(s['events'][0]['actor'],'Alice')
    def test_invalid_data_rolls_back(self):
        self.source['bottles'][0]['remaining']=-1
        with self.assertRaises(DomainError):self.repo.execute(self.command())
        self.assertEqual(self.repo.read()['refrigerators'],[])
    def test_missing_fridge_and_duplicate(self):
        self.source['bottles'][0]['fridgeId']='missing'
        with self.assertRaises(DomainError):self.repo.execute(self.command())
        self.source['bottles'][0]['fridgeId']='f';self.source['bottles'].append(self.source['bottles'][0])
        with self.assertRaises(DomainError):self.repo.execute(self.command())
