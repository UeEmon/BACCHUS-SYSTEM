import base64
import io
import json
import urllib.error
import unittest
from unittest.mock import patch
from server.recognition import GeminiRecognizer, RecognitionError

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
