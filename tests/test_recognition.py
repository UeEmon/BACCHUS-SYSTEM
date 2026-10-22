import base64
import io
import json
import urllib.error
import unittest
from unittest.mock import patch
import tempfile
from server.recognition import GeminiRecognizer, RecognitionError, choose_model
from server.repository import SQLiteRepository

class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict('os.environ',{'GEMINI_API_KEY':'test-secret','GEMINI_MODEL':'test-model'});self.env.start()
        self.photo='data:image/jpeg;base64,'+base64.b64encode(b'\xff\xd8test').decode()
    def tearDown(self):self.env.stop()
    def response(self,value):return io.BytesIO(json.dumps(value).encode())
    def success(self,value):return {'candidates':[{'content':{'parts':[{'text':json.dumps(value)}]}}]}
    def test_extraction_and_no_fabricated_volume(self):
        result=self.success(dict(name='試験酒',volume='720',brewery=None))
        with patch('urllib.request.urlopen',return_value=self.response(result)) as network:
            parsed=GeminiRecognizer().recognize(self.photo)
        self.assertEqual(parsed['name'],'試験酒');self.assertIsNone(parsed['volume']);self.assertEqual(parsed['brewery'],'')
        req=network.call_args.args[0]
        self.assertNotIn('test-secret',req.full_url);self.assertEqual(req.get_header('X-goog-api-key'),'test-secret')
        self.assertEqual(json.loads(req.data)['contents'][0]['parts'][1]['inline_data']['mime_type'],'image/jpeg')
    def test_malformed_responses(self):
        for body in [None,{},dict(candidates=[]),dict(candidates=None),self.success(['not','object'])]:
            with self.subTest(body=body),patch('urllib.request.urlopen',return_value=self.response(body)):
                with self.assertRaises(RecognitionError):GeminiRecognizer().recognize(self.photo)
    def test_rate_limit_and_timeout(self):
        for error in [urllib.error.HTTPError('test',429,'limited',{},None),TimeoutError()]:
            with patch('urllib.request.urlopen',side_effect=error):
                with self.assertRaises(RecognitionError):GeminiRecognizer().recognize(self.photo)
    def test_invalid_image_not_sent(self):
        with patch('urllib.request.urlopen') as network:
            with self.assertRaises(RecognitionError):GeminiRecognizer().recognize('data:image/jpeg;base64,!!!')
            network.assert_not_called()
    def test_missing_config(self):
        with patch.dict('os.environ',{'GEMINI_API_KEY':''}):
            with self.assertRaises(RecognitionError):GeminiRecognizer().recognize(self.photo)

def listing(*names,**extra):
    return io.BytesIO(json.dumps(dict(models=[dict(name='models/'+n,supportedGenerationMethods=['generateContent']) for n in names],**extra)).encode())

class ModelChoiceTests(unittest.TestCase):
    def test_prefers_newest_stable_flash(self):
        names=['gemini-2.5-flash','gemini-3.5-flash','gemini-3.5-pro','gemini-3.6-flash-lite','gemini-3.7-flash-preview-09-2026','gemini-3.6-flash-preview-tts','gemini-3.6-flash-image','gemini-flash-latest','gemma-3-27b-it']
        self.assertEqual(choose_model(names),'gemini-3.5-flash')
        self.assertEqual(choose_model(['gemini-3.7-flash-preview','gemini-3.6-pro-preview']),'gemini-3.7-flash-preview')
        self.assertEqual(choose_model(['gemini-10.0-flash','gemini-9.5-flash']),'gemini-10.0-flash')
        self.assertIsNone(choose_model(['gemma-3','gemini-2.5-flash-preview-tts']))

class WebSettingsTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict('os.environ',{'GEMINI_API_KEY':'','GEMINI_MODEL':''});self.env.start()
        self.tmp=tempfile.TemporaryDirectory();self.repo=SQLiteRepository(self.tmp.name+'/db');self.ai=GeminiRecognizer(self.repo)
        self.key='AIzaTestKey_0123456789abcdef'
        self.photo='data:image/jpeg;base64,'+base64.b64encode(b'\xff\xd8test').decode()
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def test_configure_auto_paginates_and_hides_key(self):
        with patch('urllib.request.urlopen',side_effect=[listing('gemini-2.5-flash',nextPageToken='p2'),listing('gemini-3.5-flash','gemini-3.5-pro')]) as network:
            status=self.ai.configure(self.key)
        self.assertEqual(status['model'],'gemini-3.5-flash');self.assertTrue(status['auto']);self.assertEqual(status['source'],'web')
        self.assertEqual(status['keyHint'],'…cdef');self.assertNotIn(self.key,json.dumps(status))
        self.assertIn('pageToken=p2',network.call_args_list[1].args[0].full_url);self.assertNotIn(self.key,network.call_args_list[0].args[0].full_url)
        self.assertNotIn(self.key,json.dumps(self.repo.read()))
    def test_manual_model_and_keep_key(self):
        with patch('urllib.request.urlopen',return_value=listing('gemini-3.5-flash','gemini-3.5-pro')):self.ai.configure(self.key)
        with patch('urllib.request.urlopen',return_value=listing('gemini-3.5-flash','gemini-3.5-pro')):status=self.ai.configure(None,'gemini-3.5-pro')
        self.assertEqual(status['model'],'gemini-3.5-pro');self.assertFalse(status['auto'])
        with patch('urllib.request.urlopen',return_value=listing('gemini-3.5-flash')):
            with self.assertRaises(RecognitionError):self.ai.configure(None,'gemini-3.5-pro')
        self.assertEqual(self.ai.status()['model'],'gemini-3.5-pro')
    def test_rejected_key_is_not_saved(self):
        with patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError('u',403,'denied',{},None)):
            with self.assertRaises(RecognitionError):self.ai.configure(self.key)
        self.assertFalse(self.ai.status()['configured'])
        for bad in ['short','x'*20+' ;']:
            with self.subTest(bad=bad),patch('urllib.request.urlopen') as network:
                with self.assertRaises(RecognitionError):self.ai.configure(bad)
                network.assert_not_called()
    def test_web_settings_override_env_and_clear(self):
        with patch.dict('os.environ',{'GEMINI_API_KEY':'env-key','GEMINI_MODEL':'gemini-2.5-flash'}):
            self.assertEqual(self.ai.status()['source'],'env')
            with patch('urllib.request.urlopen',return_value=listing('gemini-3.5-flash')):self.ai.configure(self.key)
            self.assertEqual(self.ai.config()[:2],(self.key,'gemini-3.5-flash'))
            self.assertEqual(self.ai.clear()['source'],'env')
    def test_retired_model_is_replaced_once(self):
        with patch('urllib.request.urlopen',return_value=listing('gemini-3.5-flash')):self.ai.configure(self.key)
        answer=io.BytesIO(json.dumps({'candidates':[{'content':{'parts':[{'text':json.dumps({'name':'試験酒'})}]}}]}).encode())
        with patch('urllib.request.urlopen',side_effect=[urllib.error.HTTPError('u',404,'gone',{},None),listing('gemini-3.6-flash'),answer]) as network:
            result=self.ai.recognize(self.photo)
        self.assertEqual((result['name'],result['model']),('試験酒','gemini-3.6-flash'))
        self.assertIn('gemini-3.6-flash:generateContent',network.call_args_list[2].args[0].full_url)
        with patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError('u',404,'gone',{},None)):
            with self.assertRaises(RecognitionError):self.ai.recognize(self.photo)
