import json
import os
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit
from .domain import DomainError
from .repository import SQLiteRepository
from .recognition import LabelRecognizer, RecognitionError
from .accounts import AccountService, AccessError, parse_env_users

WEB=Path(__file__).resolve().parent.parent/'web'

class Application:
    def __init__(self):
        self.repository=SQLiteRepository(os.environ.get('DATABASE_PATH','data/bacchus.sqlite3'))
        self.accounts=AccountService(self.repository,parse_env_users(os.environ.get('BACCHUS_USERS') or '{}'))
        self.recognizer=LabelRecognizer(self.repository)
    def user(self,header):
        return self.accounts.authenticate(header)

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
            app=self.server.application
            try:
                if path=='/api/session':return self.json(200,{**app.accounts.session(user),'aiConfigured':app.recognizer.status()['configured']})
                if path=='/api/ai-settings':return self.json(200,app.recognizer.status())
                if path=='/api/state':return self.json(200,app.repository.read(app.accounts.tenant_of(user)))
                if path=='/api/admin':return self.json(200,app.accounts.overview(user))
            except AccessError as error:return self.json(403,{'error':str(error)})
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
            app=self.server.application
            if path=='/api/commands':
                tenant=app.accounts.tenant_of(user);app.accounts.check_command(user,data)
                data['actor']=user['name']
                return self.json(200,app.repository.execute(tenant,data))
            if path=='/api/admin':return self.json(200,app.accounts.act(user,data))
            if path=='/api/ai-settings':
                app.accounts.require_system_admin(user,'AI設定の変更は全体管理者のみ可能です')
                recognizer=self.server.application.recognizer
                provider=data.get('provider') or None
                if data.get('clear') is True:return self.json(200,recognizer.clear(provider))
                return self.json(200,recognizer.configure(data.get('apiKey') or None,data.get('model','auto'),provider))
            if path=='/api/recognize':return self.json(200,self.server.application.recognizer.recognize(data.get('photo')))
            return self.json(404,{'error':'対象がありません'})
        except AccessError as error:self.json(403,{'error':str(error)})
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
    if not server.application.accounts.has_system_admin():
        print('注意: 全体管理者がいません。BACCHUS_USERSに {"トークン":{"name":"運営","role":"system_admin"}} を追加してください（テナント作成・AI設定に必要）',flush=True)
    print(f'BACCHUS-SYSTEM: http://{server.server_address[0]}:{server.server_address[1]}',flush=True)
    server.serve_forever()
