import base64
import io
import json
import urllib.error
import unittest
from unittest.mock import patch
import tempfile
from server.recognition import LabelRecognizer, RecognitionError, choose_model
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
            parsed=LabelRecognizer().recognize(self.photo)
        self.assertEqual(parsed['name'],'試験酒');self.assertIsNone(parsed['volume']);self.assertEqual(parsed['brewery'],'')
        req=network.call_args.args[0]
        self.assertNotIn('test-secret',req.full_url);self.assertEqual(req.get_header('X-goog-api-key'),'test-secret')
        self.assertEqual(json.loads(req.data)['contents'][0]['parts'][1]['inline_data']['mime_type'],'image/jpeg')
    def test_malformed_responses(self):
        for body in [None,{},dict(candidates=[]),dict(candidates=None),self.success(['not','object'])]:
            with self.subTest(body=body),patch('urllib.request.urlopen',return_value=self.response(body)):
                with self.assertRaises(RecognitionError):LabelRecognizer().recognize(self.photo)
    def test_rate_limit_and_timeout(self):
        for error in [urllib.error.HTTPError('test',429,'limited',{},None),TimeoutError()]:
            with patch('urllib.request.urlopen',side_effect=error):
                with self.assertRaises(RecognitionError):LabelRecognizer().recognize(self.photo)
    def test_invalid_image_not_sent(self):
        with patch('urllib.request.urlopen') as network:
            with self.assertRaises(RecognitionError):LabelRecognizer().recognize('data:image/jpeg;base64,!!!')
            network.assert_not_called()
    def test_missing_config(self):
        with patch.dict('os.environ',{'GEMINI_API_KEY':''}):
            with self.assertRaises(RecognitionError):LabelRecognizer().recognize(self.photo)

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
        self.tmp=tempfile.TemporaryDirectory();self.repo=SQLiteRepository(self.tmp.name+'/db');self.ai=LabelRecognizer(self.repo)
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

def claude_listing(*models,**extra):
    caps=lambda effort:{'image_input':{'supported':True},'structured_outputs':{'supported':True},'effort':{'supported':effort,'low':{'supported':effort}}}
    return io.BytesIO(json.dumps(dict(data=[dict(id=m,capabilities=caps(m!='claude-haiku-4-5')) for m in models],**extra)).encode())

class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict('os.environ',{k:'' for k in ('GEMINI_API_KEY','GEMINI_MODEL','ANTHROPIC_API_KEY','ANTHROPIC_MODEL','OPENAI_API_KEY','OPENAI_MODEL','AI_PROVIDER')});self.env.start()
        self.tmp=tempfile.TemporaryDirectory();self.repo=SQLiteRepository(self.tmp.name+'/db');self.ai=LabelRecognizer(self.repo)
        self.photo='data:image/jpeg;base64,'+base64.b64encode(b'\xff\xd8test').decode()
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def test_model_choice_per_provider(self):
        self.assertEqual(choose_model(['claude-sonnet-5-5','claude-opus-4-8','claude-opus-5-5','claude-haiku-5-5','claude-opus-4-1-20250805','claude-3-5-sonnet-20241022','claude-fable-5-1'],'claude'),'claude-opus-5-5')
        self.assertEqual(choose_model(['claude-opus-5-5-20260901','claude-opus-5'],'claude'),'claude-opus-5')
        self.assertIsNone(choose_model(['claude-opus-4-1','claude-sonnet-4-20250514'],'claude'))
        self.assertEqual(choose_model(['gpt-5.5','gpt-5.5-mini','gpt-5-mini','gpt-5.5-nano','gpt-5.5-mini-2026-08-01','gpt-4o','gpt-5.5-pro','gpt-realtime','o3'],'openai'),'gpt-5.5-mini')
        self.assertIsNone(choose_model(['gpt-4o-mini','gpt-4-turbo','gpt-3.5-turbo'],'openai'))
    def test_claude_configure_and_recognize(self):
        key='sk-ant-api03-Test_key-0123456789abcd'
        listing_body=claude_listing('claude-opus-5-5','claude-haiku-4-5','claude-sonnet-5-5',has_more=False)
        with patch('urllib.request.urlopen',return_value=listing_body) as network:
            status=self.ai.configure(key,'auto','claude')
        req=network.call_args.args[0]
        self.assertEqual(req.get_header('X-api-key'),key);self.assertNotIn(key,req.full_url);self.assertTrue(req.get_header('Anthropic-version'))
        self.assertEqual((status['provider'],status['model'],status['keyHint']),('claude','claude-opus-5-5','…abcd'))
        self.assertNotIn(key,json.dumps(status));self.assertNotIn(key,json.dumps(self.repo.read()))
        answer={'stop_reason':'end_turn','content':[{'type':'thinking','thinking':''},{'type':'text','text':json.dumps({'name':'試験酒','volume':720})}]}
        with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(answer).encode())) as network:
            result=self.ai.recognize(self.photo)
        self.assertEqual((result['name'],result['volume'],result['provider'],result['model']),('試験酒',720,'Claude','claude-opus-5-5'))
        body=json.loads(network.call_args.args[0].data)
        self.assertTrue(network.call_args.args[0].full_url.endswith('/v1/messages'))
        self.assertEqual(body['messages'][0]['content'][0]['source']['media_type'],'image/jpeg')
        self.assertEqual(body['output_config']['format']['type'],'json_schema');self.assertEqual(body['output_config']['effort'],'low')
        self.assertEqual(body['fallbacks'],'default');self.assertNotIn('temperature',body);self.assertNotIn('thinking',body)
        # Haiku 4.5 rejects effort and the fallback parameter, so neither is sent.
        with patch('urllib.request.urlopen',return_value=claude_listing('claude-haiku-4-5')):self.ai.configure(None,'claude-haiku-4-5','claude')
        with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(answer).encode())) as network:self.ai.recognize(self.photo)
        body=json.loads(network.call_args.args[0].data)
        self.assertNotIn('effort',body['output_config']);self.assertNotIn('fallbacks',body)
    def test_claude_refusal_and_truncation(self):
        with patch.dict('os.environ',{'AI_PROVIDER':'claude','ANTHROPIC_API_KEY':'sk-ant-env-key-0123456789','ANTHROPIC_MODEL':'claude-opus-5-5'}):
            for answer in [{'stop_reason':'refusal','content':[]},{'stop_reason':'max_tokens','content':[{'type':'text','text':'{"name":'}]}]:
                with self.subTest(answer=answer),patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(answer).encode())):
                    with self.assertRaises(RecognitionError):self.ai.recognize(self.photo)
    def test_openai_configure_and_recognize(self):
        key='sk-proj-Test_key-0123456789wxyz'
        with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps({'data':[{'id':'gpt-5.5'},{'id':'gpt-5.5-mini'},{'id':'gpt-4o'}]}).encode())) as network:
            status=self.ai.configure(key,'auto','openai')
        self.assertEqual(network.call_args.args[0].get_header('Authorization'),'Bearer '+key)
        self.assertEqual((status['provider'],status['model'],status['models']),('openai','gpt-5.5-mini',['gpt-5.5','gpt-5.5-mini']))
        answer={'choices':[{'message':{'content':json.dumps({'name':'試験酒','brewery':'試験酒造'}),'refusal':None}}]}
        with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(answer).encode())) as network:
            result=self.ai.recognize(self.photo)
        self.assertEqual((result['brewery'],result['provider']),('試験酒造','OpenAI'))
        body=json.loads(network.call_args.args[0].data)
        self.assertEqual(body['response_format'],{'type':'json_object'});self.assertTrue(body['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,'))
        refused={'choices':[{'message':{'content':None,'refusal':'no'}}]}
        with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(refused).encode())):
            with self.assertRaises(RecognitionError):self.ai.recognize(self.photo)
    def test_providers_keep_separate_keys_and_switch(self):
        with patch('urllib.request.urlopen',return_value=listing('gemini-3.5-flash')):self.ai.configure('AIzaTestKey_0123456789abcdef','auto','gemini')
        with patch('urllib.request.urlopen',return_value=claude_listing('claude-opus-5-5')):self.ai.configure('sk-ant-api03-Test_key-0123456789abcd','auto','claude')
        status=self.ai.status()
        self.assertEqual(status['provider'],'claude');self.assertEqual([p['configured'] for p in status['providers']],[True,True,False])
        # Re-saving Gemini without a key reuses its stored key and makes it active again.
        with patch('urllib.request.urlopen',return_value=listing('gemini-3.5-flash')):self.assertEqual(self.ai.configure(None,'auto','gemini')['provider'],'gemini')
        self.assertEqual(self.ai.clear('claude')['providers'][1]['configured'],False);self.assertEqual(self.ai.status()['provider'],'gemini')
        with patch.dict('os.environ',{'OPENAI_API_KEY':'sk-env-key-0123456789abcdef','OPENAI_MODEL':'gpt-5.5-mini'}),patch('urllib.request.urlopen') as network:
            status=self.ai.configure(None,'auto','openai')
            network.assert_not_called()
            self.assertEqual((status['provider'],status['source'],status['model']),('openai','env','gpt-5.5-mini'))
        for bad in ['unknown',['gemini']]:
            with self.subTest(bad=bad),self.assertRaises((RecognitionError,TypeError)):self.ai.configure('AIzaTestKey_0123456789abcdef','auto',bad)
