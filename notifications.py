"""Private owner Web Push with a durable, bounded-retry outbox."""
import base64, json, os, re, threading, time
from urllib.parse import urlsplit
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import webpush
import requests

KEY_LOCK = threading.Lock()

def init(c):
 c.executescript('''
 CREATE TABLE IF NOT EXISTS push_subscriptions(id INTEGER PRIMARY KEY, endpoint TEXT UNIQUE NOT NULL, subscription TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
 CREATE TABLE IF NOT EXISTS push_jobs(id INTEGER PRIMARY KEY, subscription_id INTEGER NOT NULL REFERENCES push_subscriptions(id), order_id INTEGER REFERENCES orders(id), status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0, created REAL NOT NULL, UNIQUE(subscription_id,order_id));
 ''')

def public_key(data):
 path=data/'push-private.pem'
 with KEY_LOCK:
  if not path.exists():
   key=ec.generate_private_key(ec.SECP256R1())
   raw=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
   with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as f: f.write(raw)
  key=serialization.load_pem_private_key(path.read_bytes(),password=None)
 return base64.urlsafe_b64encode(key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)).decode().rstrip('=')

def subject():
 value=os.environ.get('BAKERY_PUSH_SUBJECT','').strip()
 if re.fullmatch(r'mailto:[^\s@]+@[^\s@]+\.[^\s@]+',value): return value
 u=urlsplit(value)
 if u.scheme=='https' and u.hostname and not u.username and not u.password: return value
 return None

def validate_subscription(value):
 try:
  endpoint=value['endpoint']; u=urlsplit(endpoint); host=u.hostname or ''
  allowed=host in ('fcm.googleapis.com','updates.push.services.mozilla.com','web.push.apple.com') or host.endswith('.push.apple.com') or host.endswith('.notify.windows.com')
  if len(endpoint)>2048 or u.scheme!='https' or u.port not in (None,443) or u.username or u.password or u.fragment or not allowed: raise ValueError()
  keys=value['keys']; clean={}
  for name,size in [('p256dh',65),('auth',16)]:
   v=keys[name]
   if not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]+={0,2}',v): raise ValueError()
   raw=base64.urlsafe_b64decode(v+'='*(-len(v)%4))
   if len(raw)!=size: raise ValueError()
   if name=='p256dh': ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),raw)
   clean[name]=v
  return {'endpoint':endpoint,'keys':clean}
 except (KeyError,TypeError,ValueError,AttributeError): raise ValueError('Не вдалося перевірити підписку. Спробуйте ввімкнути сповіщення ще раз.')

def enqueue(c,order_id):
 c.execute("INSERT OR IGNORE INTO push_jobs(subscription_id,order_id,created) SELECT id,?,? FROM push_subscriptions WHERE active=1",(order_id,time.time()))

class NoRedirectSession(requests.Session):
 def request(self,*args,**kwargs):
  kwargs['allow_redirects']=False
  return super().request(*args,**kwargs)

def deliver_one(connect,data,sender=webpush):
 if not subject(): return False
 with connect() as c:
  c.execute('BEGIN IMMEDIATE')
  row=c.execute("SELECT j.*,s.subscription,s.active FROM push_jobs j JOIN push_subscriptions s ON s.id=j.subscription_id WHERE j.status IN ('pending','sending') AND j.next_attempt<=? ORDER BY j.id LIMIT 1",(time.time(),)).fetchone()
  if not row: c.commit(); return False
  if not row['active'] or row['created']<time.time()-86400 or row['attempts']>=5:
   c.execute("UPDATE push_jobs SET status='expired' WHERE id=?",(row['id'],)); c.commit(); return True
  c.execute("UPDATE push_jobs SET status='sending',attempts=attempts+1,next_attempt=? WHERE id=?",(time.time()+120,row['id'])); c.commit()
 oid=row['order_id']
 payload={'title':f'Нове замовлення №{oid}' if oid else 'Сповіщення працюють', 'body':'Відкрийте кабінет, щоб переглянути замовлення.' if oid else 'Тепер ви можете отримувати повідомлення про нові замовлення.', 'url':f'/admin#order-{oid}' if oid else '/admin', 'tag':f'order-{oid}' if oid else f'test-{row["id"]}'}
 status='sent'; deactivate=False
 try:
  public_key(data)
  with NoRedirectSession() as session:
   result=sender(subscription_info=json.loads(row['subscription']),data=json.dumps(payload,ensure_ascii=False),vapid_private_key=str(data/'push-private.pem'),vapid_claims={'sub':subject()},ttl=86400,timeout=15,requests_session=session)
   if result is not None and not 200<=result.status_code<300: raise RuntimeError('Push rejected')
 except Exception as exc:
  response=getattr(exc,'response',None); code=getattr(response,'status_code',None)
  deactivate=code in (404,410)
  status='failed' if deactivate or row['attempts']>=4 or (code is not None and 400<=code<500 and code!=429) else 'pending'
 with connect() as c:
  c.execute('UPDATE push_jobs SET status=?,next_attempt=? WHERE id=?',(status,time.time()+min(3600,30*2**row['attempts']),row['id']))
  if deactivate: c.execute('UPDATE push_subscriptions SET active=0 WHERE id=?',(row['subscription_id'],))
 return True

def start(connect,data):
 def worker():
  while True:
   try: busy=deliver_one(connect,data)
   except Exception: busy=False # Never log endpoints, keys or customer data.
   time.sleep(.2 if busy else 2)
 threading.Thread(target=worker,daemon=True,name='bakery-push').start()
