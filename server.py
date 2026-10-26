"""Мамина пекарня — single-owner, local-first bakery application."""
import base64, hashlib, hmac, io, json, os, re, secrets, sqlite3, threading, time
from datetime import datetime, date
from zoneinfo import ZoneInfo
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from PIL import Image, ImageOps, UnidentifiedImageError
import configuration
import notifications

ROOT = Path(__file__).resolve().parent
configuration.load_env(ROOT / '.env')
DATA = Path(os.environ.get('BAKERY_DATA', ROOT / 'data'))
DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
(DATA / 'uploads').mkdir(exist_ok=True)
DB = DATA / 'bakery.sqlite3'
KYIV = ZoneInfo('Europe/Kyiv')
STATUSES = ['Нове','Потребує уточнення','Підтверджено','Готується','Готово','Виконано','Скасовано']
CATEGORIES = ['Святкові торти','Дитячі торти','Шоколадні торти','Домашні десерти','Великодня випічка','Інші солодощі','Закуски']

def now(): return datetime.now(KYIV).isoformat(timespec='seconds')
def connect():
 c = sqlite3.connect(DB, timeout=20, isolation_level=None); c.row_factory=sqlite3.Row
 c.execute('PRAGMA foreign_keys=ON'); return c


def init():
 with connect() as c:
  c.executescript('''
  PRAGMA journal_mode=WAL;
  CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS admin (id INTEGER PRIMARY KEY CHECK(id=1), salt TEXT NOT NULL, hash TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, expires REAL NOT NULL);
  CREATE TABLE IF NOT EXISTS limits (key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL);
  CREATE TABLE IF NOT EXISTS photos (id TEXT PRIMARY KEY, public INTEGER NOT NULL, created REAL NOT NULL);
  CREATE TABLE IF NOT EXISTS gallery (id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL, photos TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, photo TEXT NOT NULL, price INTEGER NOT NULL CHECK(price>0), stock INTEGER NOT NULL CHECK(stock>=0), pickup TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, demo INTEGER NOT NULL DEFAULT 1);
  CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, token TEXT UNIQUE NOT NULL, request_key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, phone TEXT NOT NULL, details TEXT NOT NULL, product_id INTEGER REFERENCES products(id), quantity INTEGER NOT NULL DEFAULT 1, total INTEGER NOT NULL DEFAULT 0 CHECK(total>=0), direct_cost INTEGER NOT NULL DEFAULT 0 CHECK(direct_cost>=0), other_cost INTEGER NOT NULL DEFAULT 0 CHECK(other_cost>=0), status TEXT NOT NULL DEFAULT 'Нове', notes TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, completed TEXT, stock_released INTEGER NOT NULL DEFAULT 0);
  CREATE TABLE IF NOT EXISTS expenses (id INTEGER PRIMARY KEY, description TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>0), date TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS recipes (id INTEGER PRIMARY KEY, name TEXT NOT NULL, ingredients TEXT NOT NULL DEFAULT '[]');
  ''')
  notifications.init(c)
  # Additive migrations preserve existing products, orders and manually entered costs.
  for table in ('products','orders'):
   if 'unit_cost' not in {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}:
    c.execute(f'ALTER TABLE {table} ADD COLUMN unit_cost INTEGER CHECK(unit_cost>=0)')
  for column,definition in [('email',"TEXT NOT NULL DEFAULT ''"),('email_state',"TEXT NOT NULL DEFAULT 'not_requested'")]:
   if column not in {r['name'] for r in c.execute('PRAGMA table_info(orders)')}:
    c.execute(f'ALTER TABLE orders ADD COLUMN {column} {definition}')
  if 'ingredients_allergens' not in {r['name'] for r in c.execute('PRAGMA table_info(products)')}:
   c.execute("ALTER TABLE products ADD COLUMN ingredients_allergens TEXT NOT NULL DEFAULT ''")
  # Email was retired. Preserve historical order data, but invalidate recovery tokens
  # and remove queued message bodies so no old sending task can resume.
  tables={r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  if 'email_jobs' in tables:
   c.execute("UPDATE email_jobs SET status='retired',payload=NULL WHERE status IN ('queued','unavailable')")
  if 'recovery_tokens' in tables: c.execute('DELETE FROM recovery_tokens')
  c.execute('INSERT OR IGNORE INTO settings VALUES(1,?)',(json.dumps({'name':'Мамина пекарня','intro':'Печемо для ваших найтепліших моментів. Кожне замовлення обговорюємо особисто — з увагою до ваших побажань.','phone':'','pickup':'Місце та час самовивозу узгоджуємо особисто.','demo':True},ensure_ascii=False),))
  if not c.execute('SELECT 1 FROM gallery').fetchone() and not c.execute('SELECT 1 FROM settings WHERE value LIKE ?',('%seeded%',)).fetchone():
   items=[('Тортик із ведмедиком',1,['01-43-19']),('Рулет до сімейного чаю',3,['01-45-13']),('Святковий торт «16»',2,['01-45-28','01-45-32']),('Торт із метеликами',0,['01-45-44']),('Десерт із морквяним декором',3,['01-45-58']),('Великодня колекція',4,['01-46-57','01-47-03']),('Торт із лісовим декором',0,['01-47-29']),('Яскравий дитячий торт',1,['01-48-06'])]
   for name,cat,suffixes in items:
    photos=[]
    for suffix in suffixes:
     paths=list((ROOT/'pics').glob('*'+suffix+'.jpg'))
     if paths:
      pid=secrets.token_hex(16)+'.jpg'
      with Image.open(paths[0]) as raw:
       im=ImageOps.exif_transpose(raw).convert('RGB'); im.thumbnail((1600,1600)); im.save(DATA/'uploads'/pid,quality=87)
      c.execute('INSERT INTO photos VALUES(?,1,?)',(pid,time.time())); photos.append('/media/'+pid)
    if photos: c.execute('INSERT INTO gallery(name,category,description,photos) VALUES(?,?,?,?)',(name,CATEGORIES[cat],'Приклад опису. Склад, начинку та оформлення уточнимо під час замовлення.',json.dumps(photos)))
   rows=c.execute('SELECT * FROM gallery').fetchall()
   for idx,price,qty in [(1,18000,6),(4,12000,8),(5,28000,4)]:
    if len(rows)>idx:
     r=rows[idx]; c.execute('INSERT INTO products(name,description,photo,price,stock,pickup) VALUES(?,?,?,?,?,?)',(r['name'],'Демонстраційна пропозиція. Ціна та кількість наведені для перевірки застосунку.',json.loads(r['photos'])[0],price,qty,'Час самовивозу узгодимо після підтвердження.'))
   s=json.loads(c.execute('SELECT value FROM settings').fetchone()[0]); s['seeded']=True; c.execute('UPDATE settings SET value=?',(json.dumps(s,ensure_ascii=False),))

def password_hash(password,salt): return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
def set_password(password):
 if len(password)<12: raise ValueError('Пароль має містити щонайменше 12 символів.')
 salt=secrets.token_hex(16)
 with connect() as c:
  c.execute('INSERT OR REPLACE INTO admin VALUES(1,?,?)',(salt,password_hash(password,salt))); c.execute('DELETE FROM sessions')

def textval(body,key,limit=500,required=False):
 v=body.get(key,'')
 if not isinstance(v,str) or len(v)>limit: raise ValueError('Перевірте текстові поля: значення відсутнє або задовге.')
 v=v.strip()
 if required and not v: raise ValueError('Заповніть усі обов’язкові поля.')
 return v

def number(body,key,minimum=0,maximum=100000000):
 v=body.get(key,0)
 if isinstance(v,bool) or not isinstance(v,int) or not minimum<=v<=maximum: raise ValueError('Перевірте числові поля: значення поза допустимими межами.')
 return v

def validdate(v, future=False):
 try: parsed=date.fromisoformat(v)
 except (ValueError,TypeError): raise ValueError('Вкажіть коректну дату.')
 if future and parsed<datetime.now(KYIV).date(): raise ValueError('Оберіть сьогоднішню або майбутню дату.')
 return v

def photo_allowed(c,path,public=False):
 if not isinstance(path,str) or not re.fullmatch(r'/media/[a-f0-9]{32}\.jpg',path): return False
 return bool(c.execute('SELECT 1 FROM photos WHERE id=?'+(' AND public=1' if public else ''),(path.split('/')[-1],)).fetchone())

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass # Do not put customer details or secret links in access logs.
 def send(self,status,value,ctype='application/json; charset=utf-8',headers=None):
  b=json.dumps(value,ensure_ascii=False).encode() if ctype.startswith('application/json') else value
  self.send_response(status)
  for k,v in {'Content-Type':ctype,'Content-Length':str(len(b)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'self'; img-src 'self' blob:; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",**(headers or {})}.items(): self.send_header(k,v)
  self.end_headers(); self.wfile.write(b)
 def session(self,c):
  cookie={x.strip().partition('=')[0]:x.strip().partition('=')[2] for x in self.headers.get('Cookie','').split(';')}
  token=hashlib.sha256(cookie.get('bakery','').encode()).hexdigest()
  return bool(c.execute('SELECT 1 FROM sessions WHERE token=? AND expires>?',(token,time.time())).fetchone())
 def limit(self,c,label,count,seconds):
  key=label+':'+self.client_address[0]; t=time.time()
  c.execute('INSERT INTO limits VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=CASE WHEN expires<? THEN 1 ELSE count+1 END, expires=CASE WHEN expires<? THEN excluded.expires ELSE expires END',(key,t+seconds,t,t))
  if c.execute('SELECT count FROM limits WHERE key=?',(key,)).fetchone()[0]>count: raise ValueError('Забагато спроб. Зачекайте й спробуйте пізніше.')
 def do_GET(self):
  path=urlparse(self.path).path
  try:
   with connect() as c:
    if path=='/api/public':
     s=json.loads(c.execute('SELECT value FROM settings').fetchone()[0]); s.pop('seeded',None)
     return self.send(200,{'settings':s,'products':[dict(r) for r in c.execute('SELECT id,name,description,photo,price,stock,pickup,active,demo,ingredients_allergens FROM products WHERE active=1')],'gallery':[{**dict(r),'photos':json.loads(r['photos'])} for r in c.execute('SELECT * FROM gallery')],'categories':CATEGORIES,'today':datetime.now(KYIV).date().isoformat()})
    if path=='/api/admin':
     if not self.session(c): return self.send(401,{'error':'Увійдіть до кабінету власниці.'})
     return self.send(200,{'orders':[{**dict(r),'details':json.loads(r['details'])} for r in c.execute('SELECT * FROM orders ORDER BY id DESC')],'products':[dict(r) for r in c.execute('SELECT * FROM products ORDER BY id DESC')],'expenses':[dict(r) for r in c.execute('SELECT * FROM expenses ORDER BY date DESC')],'statuses':STATUSES})
    if path=='/api/admin/push':
     if not self.session(c): return self.send(401,{'error':'Увійдіть до кабінету власниці.'})
     return self.send(200,{'configured':bool(notifications.subject()),'publicKey':notifications.public_key(DATA),'failed':c.execute("SELECT COUNT(*) FROM push_jobs WHERE status='failed'").fetchone()[0]})
    if path.startswith('/media/'):

     pid=path.removeprefix('/media/')
     if not re.fullmatch(r'[a-f0-9]{32}\.jpg',pid): return self.send(404,{'error':'Фото не знайдено.'})
     row=c.execute('SELECT public FROM photos WHERE id=?',(pid,)).fetchone()
     if not row or (not row['public'] and not self.session(c)): return self.send(404,{'error':'Фото не знайдено.'})
     return self.send(200,(DATA/'uploads'/pid).read_bytes(),'image/jpeg')
   files={'/':'index.html','/admin':'index.html','/status':'index.html','/recover':'index.html','/app.js':'app.js','/style.css':'style.css','/sw.js':'sw.js','/push.js':'push.js','/manifest.webmanifest':'manifest.webmanifest','/icon-192.png':'icon-192.png','/icon-512.png':'icon-512.png'}
   if path not in files: return self.send(404,{'error':'Сторінку не знайдено.'})
   mime='text/html; charset=utf-8' if files[path].endswith('html') else ('text/css; charset=utf-8' if path.endswith('css') else 'text/javascript; charset=utf-8')
   if path.endswith('.webmanifest'): mime='application/manifest+json'
   if path.endswith('.png'): mime='image/png'
   self.send(200,(ROOT/'static'/files[path]).read_bytes(),mime)
  except Exception: self.send(500,{'error':'Не вдалося завантажити дані.'})
 def do_POST(self):
  try:
   if self.headers.get('X-Bakery-Request')!='1': return self.send(403,{'error':'Оновіть сторінку та спробуйте ще раз.'})
   origin=self.headers.get('Origin')
   if origin and urlparse(origin).netloc!=self.headers.get('Host'): return self.send(403,{'error':'Недозволений запит.'})
   length=int(self.headers.get('Content-Length','0'))
   if length>12000000: return self.send(413,{'error':'Файл завеликий. Максимум 8 МБ.'})
   body=json.loads(self.rfile.read(length))
   if not isinstance(body,dict): raise ValueError('Некоректні дані.')
   path=urlparse(self.path).path
   with connect() as c:
    if path.startswith('/api/admin/') and not self.session(c): return self.send(401,{'error':'Увійдіть до кабінету власниці.'})
    if path in ('/api/admin/push/subscribe','/api/admin/push/disable','/api/admin/push/test','/api/admin/push/status'):
     self.limit(c,'push',60,3600)
     sub=notifications.validate_subscription(body.get('subscription')); endpoint=sub['endpoint']
     if path.endswith('/subscribe'):
      if not notifications.subject(): raise ValueError('Сповіщення ще не налаштовані на сервері. Зверніться до того, хто налаштовує сайт.')
      notifications.public_key(DATA)
      c.execute('INSERT INTO push_subscriptions(endpoint,subscription) VALUES(?,?) ON CONFLICT(endpoint) DO UPDATE SET subscription=excluded.subscription,active=1',(endpoint,json.dumps(sub)))
     elif path.endswith('/disable'): c.execute('UPDATE push_subscriptions SET active=0 WHERE endpoint=?',(endpoint,))
     elif path.endswith('/test'):
      self.limit(c,'push-test',5,3600)
      row=c.execute('SELECT id FROM push_subscriptions WHERE endpoint=? AND active=1',(endpoint,)).fetchone()
      if not row: raise ValueError('Спочатку ввімкніть сповіщення на цьому пристрої.')
      c.execute('INSERT INTO push_jobs(subscription_id,created) VALUES(?,?)',(row['id'],time.time()))
     row=c.execute('SELECT id,active FROM push_subscriptions WHERE endpoint=?',(endpoint,)).fetchone()
     job=c.execute('SELECT status FROM push_jobs WHERE subscription_id=? ORDER BY id DESC LIMIT 1',(row['id'],)).fetchone() if row else None
     return self.send(200,{'active':bool(row and row['active']),'lastStatus':job['status'] if job else None})
    if path=='/api/login':
     self.limit(c,'login',8,900)
     row=c.execute('SELECT * FROM admin').fetchone(); pw=textval(body,'password',200,True)
     if not row or not hmac.compare_digest(row['hash'],password_hash(pw,row['salt'])): return self.send(401,{'error':'Неправильний пароль.'})
     token=secrets.token_urlsafe(32); c.execute('INSERT INTO sessions VALUES(?,?)',(hashlib.sha256(token.encode()).hexdigest(),time.time()+43200))
     secure='; Secure' if os.environ.get('BAKERY_SECURE_COOKIE')=='1' else ''
     return self.send(200,{'ok':True},headers={'Set-Cookie':f'bakery={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200{secure}'})
    if path=='/api/logout':
     raw=self.headers.get('Cookie',''); match=re.search(r'(?:^|;\s*)bakery=([^;]+)',raw)
     if match: c.execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(match[1].encode()).hexdigest(),))
     return self.send(200,{'ok':True},headers={'Set-Cookie':'bakery=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'})
    if path=='/api/upload':
     self.limit(c,'upload',40,3600)
     public=bool(body.get('public',False))
     if public and not self.session(c): return self.send(401,{'error':'Увійдіть до кабінету.'})
     raw=base64.b64decode(textval(body,'data',11500000,True),validate=True)
     if len(raw)>8*1024*1024: raise ValueError('Фото має бути менше 8 МБ.')
     Image.MAX_IMAGE_PIXELS=25000000
     try:
      with Image.open(io.BytesIO(raw)) as source:
       if source.format not in ['JPEG','PNG','WEBP']: raise ValueError('Дозволені лише JPEG, PNG та WebP.')
       if source.width*source.height>25000000: raise ValueError('Фото завелике: максимум 25 мегапікселів.')
       im=ImageOps.exif_transpose(source).convert('RGB'); im.thumbnail((1600,1600)); pid=secrets.token_hex(16)+'.jpg'; im.save(DATA/'uploads'/pid,quality=87)
     except (UnidentifiedImageError,OSError,Image.DecompressionBombError): raise ValueError('Не вдалося прочитати фото. Оберіть JPEG, PNG або WebP.')
     c.execute('INSERT INTO photos VALUES(?,?,?)',(pid,int(public),time.time()))
     return self.send(200,{'url':'/media/'+pid})
    if path in ['/api/reserve','/api/custom']:
     self.limit(c,'order',30,3600)
     name=textval(body,'name',100,True); phone=textval(body,'phone',30,True)
     if not re.fullmatch(r'\+?[\d\s()\-]{10,25}',phone) or not 10<=len(re.sub(r'\D','',phone))<=15: raise ValueError('Вкажіть коректний номер телефону.')
     key=textval(body,'request_key',100,True)
     if len(key)<16: raise ValueError('Оновіть форму.')
     c.execute('BEGIN IMMEDIATE')
     previous=c.execute('SELECT id,token FROM orders WHERE request_key=?',(key,)).fetchone()
     if previous: c.commit(); return self.send(200,{'token':previous['token'],'id':previous['id']})
     token=secrets.token_urlsafe(32); quantity=1; product=None; total=0; unit_cost=None; direct_cost=0
     if path=='/api/reserve':
      if any(body.get(k) for k in ('allergies','diet','flavours','comments','sugar_free','inspiration','product_inspiration')):
       raise ValueError('Готовий десерт не можна змінити. Для особливих побажань оберіть «Замовити схожий».')
      product=number(body,'product_id',1); quantity=number(body,'quantity',1,100)
      p=c.execute('SELECT * FROM products WHERE id=? AND active=1',(product,)).fetchone()
      if not p or p['stock']<quantity: c.rollback(); return self.send(409,{'error':'На жаль, такої кількості вже немає. Оновіть залишки та оберіть менше.'})
      c.execute('UPDATE products SET stock=stock-? WHERE id=?',(quantity,product)); total=p['price']*quantity
      unit_cost=p['unit_cost']; direct_cost=(unit_cost or 0)*quantity
      details={'product':p['name'],'pickup':p['pickup'],'demo':bool(p['demo']),'ingredients_allergens':p['ingredients_allergens'],'date':validdate(textval(body,'date',10,True),True)}
     else:
      details={k:textval(body,k,2000,k=='occasion') for k in ['occasion','flavours','diet','allergies','comments']}
      details['date']=validdate(textval(body,'date',10,True),True); details['guests']=number(body,'guests',1,1000); details['budget']=number(body,'budget',0); details['sugar_free']=bool(body.get('sugar_free',False))
      photos=body.get('photos',[])
      if not isinstance(photos,list) or len(photos)>4 or any(not photo_allowed(c,p) for p in photos): raise ValueError('Перевірте фото: не більше чотирьох.')
      details['photos']=photos
      product_inspiration=body.get('product_inspiration')
      if product_inspiration:
       if body.get('inspiration'): raise ValueError('Оберіть один десерт для натхнення.')
       source=c.execute('SELECT id,name,photo FROM products WHERE id=? AND active=1',(number(body,'product_inspiration',1),)).fetchone()
       if not source: raise ValueError('Десерт для натхнення не знайдено. Оновіть сторінку.')
       details['inspiration']={'type':'product','id':source['id'],'name':source['name'],'photos':[source['photo']] if source['photo'] else []}
      inspiration=body.get('inspiration')
      if inspiration:
       g=c.execute('SELECT * FROM gallery WHERE id=?',(number(body,'inspiration',1),)).fetchone()
       if not g: raise ValueError('Роботу не знайдено.')
       details['inspiration']={'name':g['name'],'photos':json.loads(g['photos'])}
     cursor=c.execute('INSERT INTO orders(token,request_key,kind,name,phone,details,product_id,quantity,total,direct_cost,unit_cost,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(token,key,'reservation' if product else 'custom',name,phone,json.dumps(details,ensure_ascii=False),product,quantity,total,direct_cost,unit_cost,now()))
     notifications.enqueue(c,cursor.lastrowid)
     c.commit()
     return self.send(200,{'token':token,'id':cursor.lastrowid})
    if path=='/api/status':
     self.limit(c,'status',100,3600)
     r=c.execute('SELECT id,status,kind,total,details FROM orders WHERE token=?',(textval(body,'token',100,True),)).fetchone()
     if not r: return self.send(404,{'error':'Замовлення не знайдено. Перевірте посилання.'})
     d=json.loads(r['details']); return self.send(200,{'id':r['id'],'status':r['status'],'total':r['total'],'kind':r['kind'],'date':d['date'],'product':d.get('product','Особливий торт')})
    if path=='/api/admin/product':
     name=textval(body,'name',150,True); description=textval(body,'description',2000); price=number(body,'price',1); stock=number(body,'stock',0,10000); pickup=textval(body,'pickup',500,True); photo=textval(body,'photo',100,True)
     if not photo_allowed(c,photo,True): raise ValueError('Додайте фотографію десерту.')
     unit_cost=None if body.get('unit_cost') is None else number(body,'unit_cost')
     ingredients_allergens=textval(body,'ingredients_allergens',3000)
     values=(name,description,photo,price,stock,pickup,int(bool(body.get('active'))),int(bool(body.get('demo'))),unit_cost,ingredients_allergens)
     if body.get('id'):
      c.execute('UPDATE products SET name=?,description=?,photo=?,price=?,stock=?,pickup=?,active=?,demo=?,unit_cost=?,ingredients_allergens=? WHERE id=?',(*values,number(body,'id',1)))
     else: c.execute('INSERT INTO products(name,description,photo,price,stock,pickup,active,demo,unit_cost,ingredients_allergens) VALUES(?,?,?,?,?,?,?,?,?,?)',values)
    elif path=='/api/admin/order':
     oid=number(body,'id',1); status=textval(body,'status',40,True)
     if status not in STATUSES: raise ValueError('Невідомий статус.')
     total=number(body,'total'); direct=number(body,'direct_cost'); other=number(body,'other_cost'); notes=textval(body,'notes',4000)
     c.execute('BEGIN IMMEDIATE'); old=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
     if not old: raise ValueError('Замовлення не знайдено.')
     if old['status']=='Скасовано' and status!='Скасовано': raise ValueError('Скасоване замовлення не можна відновити. Створіть нове.')
     if status=='Скасовано' and old['product_id'] and not old['stock_released']:
      c.execute('UPDATE products SET stock=stock+? WHERE id=?',(old['quantity'],old['product_id'])); c.execute('UPDATE orders SET stock_released=1 WHERE id=?',(oid,))
     completed=(old['completed'] or now()) if status=='Виконано' else None
     c.execute('UPDATE orders SET status=?,total=?,direct_cost=?,other_cost=?,notes=?,completed=? WHERE id=?',(status,total,direct,other,notes,completed,oid)); c.commit()
    elif path=='/api/admin/expense':
     c.execute('INSERT INTO expenses(description,amount,date) VALUES(?,?,?)',(textval(body,'description',300,True),number(body,'amount',1),validdate(textval(body,'date',10,True))))
    elif path=='/api/admin/expense/delete': c.execute('DELETE FROM expenses WHERE id=?',(number(body,'id',1),))
    elif path=='/api/admin/settings':
     s={k:textval(body,k,2000,k=='name') for k in ['name','intro','phone','pickup']}; s['demo']=bool(body.get('demo')); s['seeded']=True
     c.execute('UPDATE settings SET value=?',(json.dumps(s,ensure_ascii=False),))
    elif path=='/api/admin/gallery':
     name=textval(body,'name',150,True); category=textval(body,'category',80,True); description=textval(body,'description',2000,True); photos=body.get('photos',[])
     if category not in CATEGORIES or not isinstance(photos,list) or not 1<=len(photos)<=4 or any(not photo_allowed(c,p,True) for p in photos): raise ValueError('Оберіть категорію та додайте 1–4 фотографії.')
     if body.get('id'): c.execute('UPDATE gallery SET name=?,category=?,description=?,photos=? WHERE id=?',(name,category,description,json.dumps(photos),number(body,'id',1)))
     else: c.execute('INSERT INTO gallery(name,category,description,photos) VALUES(?,?,?,?)',(name,category,description,json.dumps(photos)))
    else: return self.send(404,{'error':'Дію не знайдено.'})
    return self.send(200,{'ok':True})
  except (ValueError,TypeError,KeyError) as e: self.send(400,{'error':str(e) or 'Перевірте введені дані.'})
  except Exception: self.send(500,{'error':'Не вдалося зберегти. Спробуйте ще раз.'})

if __name__=='__main__':
 import argparse,getpass
 p=argparse.ArgumentParser(); p.add_argument('--set-password',action='store_true'); p.add_argument('--port',type=int,default=8000); p.add_argument('--lan',action='store_true',help='Лише для розробки: доступ через локальну мережу Wi-Fi'); args=p.parse_args(); init()
 if args.set_password:
  password=getpass.getpass('Новий пароль (від 12 символів): ')
  if password!=getpass.getpass('Повторіть пароль: '): raise SystemExit('Паролі не збігаються.')
  set_password(password); print('Пароль збережено.'); raise SystemExit
 if os.environ.get('BAKERY_ADMIN_PASSWORD'):
  with connect() as c: exists=c.execute('SELECT 1 FROM admin').fetchone()
  if not exists: set_password(os.environ['BAKERY_ADMIN_PASSWORD'])
 notifications.start(connect,DATA)
 print(f'Мамина пекарня: http://127.0.0.1:{args.port}',flush=True)
 if args.lan: print(f'Локальне тестування Wi-Fi: http://<IPv4-адреса Mac>:{args.port} — без тунелів і переадресації портів.',flush=True)
 ThreadingHTTPServer(('0.0.0.0' if args.lan else '127.0.0.1',args.port),Handler).serve_forever()
