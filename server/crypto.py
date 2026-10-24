"""Encryption of secrets at rest (AI API keys, SSO client secrets, MFA secrets) using only the
standard library.

Construction (encrypt-then-MAC): two keys are derived from the master key with HMAC-SHA256.
A random 16-byte nonce starts a keystream of HMAC-SHA256(enc_key, nonce || counter) blocks that
is XORed with the plaintext; HMAC-SHA256(mac_key, header || nonce || ciphertext || context) is the
tag. The context binds a value to its row (for example tenant and setting name), so a ciphertext
copied to another row does not decrypt. The stored form names the key, so keys can be rotated;
a key-management service can later replace this module behind the same two functions."""
import base64
import hashlib
import hmac
import os
import secrets
import sys
from pathlib import Path

PREFIX='enc:v1:'

class SecretError(ValueError):
    pass

def b64decode(text):
    return base64.urlsafe_b64decode(text+'='*(-len(text)%4))

class SecretBox:
    def __init__(self, keys):
        if not keys or any(len(k)<32 for k in keys):raise ValueError('secret keys need at least 32 bytes')
        self.keys={hashlib.sha256(k).hexdigest()[:8]:k for k in keys}
        self.current=hashlib.sha256(keys[0]).hexdigest()[:8]
    def derived(self, key_id, label):
        return hmac.new(self.keys[key_id],label,hashlib.sha256).digest()
    def stream(self, key_id, nonce, length):
        key=self.derived(key_id,b'bacchus-secret-encryption')
        return b''.join(hmac.new(key,nonce+i.to_bytes(4,'big'),hashlib.sha256).digest() for i in range((length+31)//32))[:length]
    def tag(self, key_id, nonce, ciphertext, context):
        return hmac.new(self.derived(key_id,b'bacchus-secret-authentication'),PREFIX.encode()+key_id.encode()+nonce+ciphertext+context.encode(),hashlib.sha256).digest()
    def encrypt(self, plaintext, context):
        data=plaintext.encode();nonce=secrets.token_bytes(16)
        ciphertext=bytes(a^b for a,b in zip(data,self.stream(self.current,nonce,len(data))))
        return PREFIX+self.current+':'+base64.urlsafe_b64encode(nonce+ciphertext+self.tag(self.current,nonce,ciphertext,context)).decode().rstrip('=')
    def decrypt(self, value, context):
        """Plain values written before encryption existed are returned as they are."""
        if value is None or not value.startswith(PREFIX):return value
        try:
            key_id,body=value[len(PREFIX):].split(':',1);raw=b64decode(body)
        except ValueError:raise SecretError('暗号化された値の形式が不正です') from None
        if key_id not in self.keys:raise SecretError('暗号鍵が見つかりません（BACCHUS_SECRET_KEYを確認してください）')
        nonce,ciphertext,tag=raw[:16],raw[16:-32],raw[-32:]
        if len(raw)<48 or not hmac.compare_digest(tag,self.tag(key_id,nonce,ciphertext,context)):raise SecretError('暗号化された値を検証できません（改ざんまたは鍵の誤り）')
        return bytes(a^b for a,b in zip(ciphertext,self.stream(key_id,nonce,len(ciphertext)))).decode()
    def needs_rewrap(self, value):
        return value is not None and not value.startswith(PREFIX+self.current+':')

def parse_key(text):
    try:key=b64decode(text.strip())
    except ValueError:raise ValueError('BACCHUS_SECRET_KEY must be base64url') from None
    if len(key)<32:raise ValueError('BACCHUS_SECRET_KEY must hold at least 32 bytes')
    return key

def load_box(database_path):
    """BACCHUS_SECRET_KEY (current) and BACCHUS_SECRET_KEY_PREVIOUS (comma-separated, for rotation).
    Without them a key file next to the database is used, created on first start; that still keeps
    secrets out of database copies and exports, but production should hold the key elsewhere."""
    current=os.environ.get('BACCHUS_SECRET_KEY','').strip()
    previous=[parse_key(k) for k in os.environ.get('BACCHUS_SECRET_KEY_PREVIOUS','').split(',') if k.strip()]
    if current:return SecretBox([parse_key(current),*previous]),'env'
    path=Path(os.environ.get('BACCHUS_SECRET_KEY_FILE') or Path(database_path).parent/'secret.key')
    if not path.exists():
        path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as file:file.write(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
        print(f'注意: 暗号鍵を {path} に作成しました。本番ではBACCHUS_SECRET_KEYで鍵をDBとは別の場所から渡してください',file=sys.stderr,flush=True)
    return SecretBox([parse_key(path.read_text()),*previous]),'file'

def new_key():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('=')
