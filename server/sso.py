"""OpenID Connect sign-in (authorization code flow with PKCE) using only the standard library.

The ID token is taken from the token endpoint over a verified HTTPS connection, which OpenID
Connect Core 3.1.3.7 accepts in place of checking the token signature; its issuer, audience,
expiry and nonce are still checked here."""
import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

class SsoError(ValueError):
    pass

_discovery={}

def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()

def https(url):
    return isinstance(url,str) and url.startswith('https://') and len(url)<=500

def fetch_json(req):
    try:
        with urllib.request.urlopen(req,timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise SsoError(f'IDプロバイダーがエラーを返しました（HTTP {error.code}）') from None
    except (ValueError,urllib.error.URLError,TimeoutError):
        raise SsoError('IDプロバイダーに接続できません') from None

def discover(issuer):
    issuer=issuer.rstrip('/')
    cached=_discovery.get(issuer)
    if cached and cached[0]>time.time():return cached[1]
    config=fetch_json(urllib.request.Request(issuer+'/.well-known/openid-configuration',headers={'Accept':'application/json'}))
    if not isinstance(config,dict) or config.get('issuer','').rstrip('/')!=issuer or not https(config.get('authorization_endpoint')) or not https(config.get('token_endpoint')):
        raise SsoError('IDプロバイダーの設定（issuer）が一致しません')
    _discovery[issuer]=(time.time()+3600,config)
    return config

def new_request():
    """state, nonce and PKCE verifier for one sign-in attempt."""
    return secrets.token_urlsafe(32),secrets.token_urlsafe(32),secrets.token_urlsafe(48)

def authorization_url(issuer, client_id, redirect_uri, state, nonce, verifier, login_hint):
    config=discover(issuer)
    query=urllib.parse.urlencode({'response_type':'code','scope':'openid email profile','client_id':client_id,'redirect_uri':redirect_uri,'state':state,'nonce':nonce,
                                  'code_challenge':b64url(hashlib.sha256(verifier.encode()).digest()),'code_challenge_method':'S256','login_hint':login_hint})
    return config['authorization_endpoint']+('&' if '?' in config['authorization_endpoint'] else '?')+query

def verified_claims(issuer, client_id, client_secret, redirect_uri, code, verifier, nonce):
    config=discover(issuer)
    body=urllib.parse.urlencode({'grant_type':'authorization_code','code':code,'redirect_uri':redirect_uri,'client_id':client_id,'client_secret':client_secret,'code_verifier':verifier}).encode()
    result=fetch_json(urllib.request.Request(config['token_endpoint'],data=body,headers={'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'}))
    try:
        payload=result['id_token'].split('.')[1]
        claims=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
    except (KeyError,IndexError,AttributeError,TypeError,ValueError):
        raise SsoError('IDトークンを読み取れません') from None
    audience=claims.get('aud')
    audience=audience if isinstance(audience,list) else [audience]
    if claims.get('iss','').rstrip('/')!=config['issuer'].rstrip('/'):raise SsoError('IDトークンの発行元が一致しません')
    if client_id not in audience or (len(audience)>1 and claims.get('azp')!=client_id):raise SsoError('IDトークンの宛先が一致しません')
    if not isinstance(claims.get('exp'),(int,float)) or claims['exp']<time.time()-60:raise SsoError('IDトークンの有効期限が切れています')
    if not secrets.compare_digest(str(claims.get('nonce','')),nonce):raise SsoError('ログイン要求が一致しません。もう一度ログインしてください')
    if not isinstance(claims.get('sub'),str) or not claims['sub']:raise SsoError('IDトークンに利用者IDがありません')
    return claims
