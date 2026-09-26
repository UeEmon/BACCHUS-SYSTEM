import hmac
import json
import os
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit
from .domain import DomainError
from .repository import SQLiteRepository
from .recognition import GeminiRecognizer, RecognitionError

WEB=Path(__file__).resolve().parent.parent/'web'

class Application:
    def __init__(self):
        self.repository=SQLiteRepository(os.environ.get('DATABASE_PATH','data/bacchus.sqlite3'))
        self.users=json.loads(os.environ.get('BACCHUS_USERS') or '{}')
        # tokens map to display names; no implicit unauthenticated shared access.
        if not isinstance(self.users,dict) or any(not isinstance(k,str) or len(k)<24 or not isinstance(v,str) or not v for k,v in self.users.items()):
            raise ValueError('BACCHUS_USERS requires JSON {"long-random-token": "display name"}; minimum token length 24')
        self.recognizer=GeminiRecognizer()
    def user(self,header):
        token=header.removeprefix('Bearer ')
        return next((name for key,name in self.users.items() if hmac.compare_digest(key,token)),None)

class Handler(BaseHTTPRequestHandler):
    server_version='BACCHUS'
    def json(self,status,data):
        payload=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/api/health': return self.json(200,{'ok':True})
        if path.startswith('/api/'):
            user=self.server.application.user(self.headers.get('Authorization',''))
            if not user:return self.json(401,{'error':'接続トークンが無効です'})
            if path=='/api/session':return self.json(200,{'user':user,'workspace':self.server.application.repository.workspace(),'aiConfigured':bool(os.environ.get('GEMINI_API_KEY') and os.environ.get('GEMINI_MODEL'))})
            if path=='/api/state':return self.json(200,self.server.application.repository.read())
            return self.json(404,{'error':'対象がありません'})
        # Explicit static whitelist: server files, credentials and DB are never served.
        names={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/domain.js':'domain.js','/migration.js':'migration.js','/repositories.js':'repositories.js','/styles.css':'styles.css','/manifest.webmanifest':'manifest.webmanifest','/sw.js':'sw.js','/icon.svg':'icon.svg'}
        if path not in names:return self.json(404,{'error':'対象がありません'})
        file=WEB/names[path]
        mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.webmanifest':'application/manifest+json','.svg':'image/svg+xml'}[file.suffix]
        data=file.read_bytes();self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-cache');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'");self.end_headers();self.wfile.write(data)
    def do_POST(self):
        user=self.server.application.user(self.headers.get('Authorization',''))
        if not user:return self.json(401,{'error':'接続トークンが無効です'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<=0 or length>25000000:return self.json(413,{'error':'送信データが大きすぎます'})
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict):raise DomainError('リクエストが不正です')
            path=urlsplit(self.path).path
            if path=='/api/commands':
                data['actor']=user
                return self.json(200,self.server.application.repository.execute(data))
            if path=='/api/recognize':return self.json(200,self.server.application.recognizer.recognize(data.get('photo')))
            return self.json(404,{'error':'対象がありません'})
        except DomainError as error:self.json(409,{'error':str(error)})
        except RecognitionError as error:self.json(422,{'error':str(error)})
        except (ValueError,TypeError,KeyError):self.json(400,{'error':'入力形式が不正です'})
        except Exception:
            self.json(500,{'error':'サーバーで保存できませんでした。後で再試行してください'})
    def log_message(self,format,*args):
        # Do not log URL query strings, credentials or image request bodies.
        pass

def create_server(host='127.0.0.1',port=8000):
    server=ThreadingHTTPServer((host,port),Handler);server.application=Application();return server
if __name__=='__main__':
    server=create_server(os.environ.get('HOST','127.0.0.1'),int(os.environ.get('PORT','8000')))
    print(f'BACCHUS-SYSTEM: http://{server.server_address[0]}:{server.server_address[1]}',flush=True)
    server.serve_forever()
