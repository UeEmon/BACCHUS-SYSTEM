import json
import os
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, quote
from .domain import DomainError
from .repository import SQLiteRepository
from .recognition import RecognitionError
from .sso import SsoError
from .accounts import AccountService, AccessError, AuthError, LimitError, parse_env_users

WEB=Path(__file__).resolve().parent.parent/'web'
STATIC={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/admin.js':'admin.js','/domain.js':'domain.js','/migration.js':'migration.js','/repositories.js':'repositories.js',
        '/styles.css':'styles.css','/manifest.webmanifest':'manifest.webmanifest','/sw.js':'sw.js','/icon.svg':'icon.svg'}
MIME={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.webmanifest':'application/manifest+json','.svg':'image/svg+xml'}

class NotFound(Exception):
    pass

class Application:
    def __init__(self):
        self.repository=SQLiteRepository(os.environ.get('DATABASE_PATH','data/bacchus.sqlite3'))
        self.accounts=AccountService(self.repository,parse_env_users(os.environ.get('BACCHUS_USERS') or '{}'),os.environ.get('BACCHUS_PUBLIC_URL',''))

class Handler(BaseHTTPRequestHandler):
    server_version='BACCHUS'
    def json(self,status,data):
        payload=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    def redirect(self,location):
        self.send_response(302);self.send_header('Location',location);self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Length','0');self.end_headers()
    def ip(self):
        # Behind a reverse proxy the client address is the proxy's; trust its header only when told to.
        forwarded=self.headers.get('X-Forwarded-For','') if os.environ.get('BACCHUS_TRUST_PROXY')=='1' else ''
        return (forwarded.split(',')[0].strip() or self.client_address[0])[:64]
    def agent(self):
        return self.headers.get('User-Agent','')
    def handle_api(self,method,path,data):
        accounts=self.server.application.accounts;ip=self.ip()
        if method=='POST' and path=='/api/auth/login':return accounts.login(data.get('email'),data.get('password'),ip,self.agent())
        if method=='POST' and path=='/api/auth/sso/start':return accounts.start_sso(data.get('email'),ip)
        if method=='POST' and path=='/api/auth/exchange':return accounts.exchange_code(data.get('code'),ip,self.agent())
        user=accounts.authenticate(self.headers.get('Authorization',''))
        if not user:raise AuthError('ログインしてください（セッションの有効期限が切れた可能性があります）')
        if path=='/api/session':return {**accounts.session_info(user),'aiConfigured':user['tenantId'] is not None and accounts.ai_status(user)['configured']}
        if method=='POST' and path=='/api/auth/logout':accounts.logout(user,ip);return {}
        if method=='POST' and path=='/api/auth/password':accounts.change_password(user,data.get('currentPassword'),data.get('newPassword'),ip);return {}
        accounts.require_ready(user)
        routes={('GET','/api/state'):lambda:self.server.application.repository.read(accounts.tenant_of(user)),
                ('POST','/api/commands'):lambda:accounts.execute(user,data,ip),
                ('POST','/api/recognize'):lambda:accounts.recognize(user,data.get('photo'),ip),
                ('GET','/api/ai-settings'):lambda:accounts.ai_status(user),
                ('POST','/api/ai-settings'):lambda:accounts.configure_ai(user,data,ip),
                ('GET','/api/system'):lambda:accounts.system_overview(user),
                ('POST','/api/system'):lambda:accounts.system_action(user,data,ip),
                ('GET','/api/tenant'):lambda:accounts.tenant_overview(user),
                ('POST','/api/tenant'):lambda:accounts.tenant_action(user,data,ip)}
        route=routes.get((method,path))
        if not route:raise NotFound()
        return route()
    def respond(self,method,data=None):
        path=urlsplit(self.path).path
        try:return self.json(200,self.handle_api(method,path,data or {}))
        except AuthError as error:self.json(401,{'error':str(error)})
        except AccessError as error:self.json(403,{'error':str(error)})
        except LimitError as error:self.json(429,{'error':str(error)})
        except NotFound:self.json(404,{'error':'対象がありません'})
        except DomainError as error:self.json(409,{'error':str(error)})
        except (RecognitionError,SsoError) as error:self.json(422,{'error':str(error)})
        except (ValueError,TypeError,KeyError):self.json(400,{'error':'入力形式が不正です'})
        except Exception:self.json(500,{'error':'サーバーで処理できませんでした。後で再試行してください'})
    def do_GET(self):
        url=urlsplit(self.path);path=url.path
        if path=='/api/health':return self.json(200,{'ok':True})
        if path=='/api/auth/sso/callback':
            # The identity provider returns here; the page receives only a short-lived one-time code.
            query=parse_qs(url.query)
            try:
                if query.get('error'):raise AuthError('IDプロバイダーでログインが取り消されました')
                code=self.server.application.accounts.finish_sso(query.get('state',[''])[0],query.get('code',[''])[0],self.ip())
                return self.redirect('/#sso='+quote(code))
            except (AuthError,SsoError,DomainError) as error:return self.redirect('/#sso_error='+quote(str(error)))
            except Exception:return self.redirect('/#sso_error='+quote('SSOでログインできませんでした'))
        if path.startswith('/api/'):return self.respond('GET')
        # Explicit static whitelist: server files, credentials and DB are never served.
        if path not in STATIC:return self.json(404,{'error':'対象がありません'})
        file=WEB/STATIC[path]
        data=file.read_bytes();self.send_response(200);self.send_header('Content-Type',MIME[file.suffix]);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-cache');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'");self.end_headers();self.wfile.write(data)
    def do_POST(self):
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<=0 or length>25000000:return self.json(413,{'error':'送信データが大きすぎます'})
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict):raise ValueError()
        except ValueError:return self.json(400,{'error':'入力形式が不正です'})
        self.respond('POST',data)
    def log_message(self,format,*args):
        # Do not log URL query strings, credentials or image request bodies.
        pass

def create_server(host='127.0.0.1',port=8000):
    server=ThreadingHTTPServer((host,port),Handler);server.application=Application();return server
if __name__=='__main__':
    server=create_server(os.environ.get('HOST','127.0.0.1'),int(os.environ.get('PORT','8000')))
    if not server.application.accounts.has_system_admin():
        print('注意: 全体管理者がいません。BACCHUS_USERSに {"トークン":{"name":"運営","role":"system_admin"}} を追加してください（テナントの作成に必要）',flush=True)
    print(f'BACCHUS-SYSTEM: http://{server.server_address[0]}:{server.server_address[1]}',flush=True)
    server.serve_forever()
