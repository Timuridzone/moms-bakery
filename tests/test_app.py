import os,tempfile,unittest,threading,json,urllib.request,urllib.error,concurrent.futures,uuid,sys,io,base64
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
TEMP=tempfile.TemporaryDirectory();os.environ['BAKERY_DATA']=TEMP.name
import server
from PIL import Image
class BakeryTest(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  server.init();server.set_password('test-password-12345')
  cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
  cls.url='http://127.0.0.1:'+str(cls.http.server_port)
  threading.Thread(target=cls.http.serve_forever,daemon=True).start()
  _,_,headers=cls.req('/api/login',{'password':'test-password-12345'})
  cls.cookie=headers['Set-Cookie'].split(';')[0]
 @classmethod
 def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();TEMP.cleanup()
 @classmethod
 def req(cls,path,data=None,admin=False,extra=None):
  headers={'Content-Type':'application/json','X-Bakery-Request':'1'}
  if admin:headers['Cookie']=cls.cookie
  headers.update(extra or {})
  req=urllib.request.Request(cls.url+path,json.dumps(data).encode() if data is not None else None,headers)
  try:r=urllib.request.urlopen(req)
  except urllib.error.HTTPError as e:r=e
  raw=r.read()
  return r.status,json.loads(raw) if 'application/json' in r.headers.get('Content-Type','') else raw,r.headers
 def new_product(self,stock=3):
  with server.connect() as c:
   pid=c.execute('INSERT INTO products(name,description,photo,price,stock,pickup) VALUES(?,?,?,?,?,?)',('Тест','Приклад','',15000,stock,'Узгодимо')).lastrowid
  return pid
 def reserve(self,pid,qty=1,key=None):
  return self.req('/api/reserve',{'name':'Олена','phone':'+380671234567','date':server.datetime.now(server.KYIV).date().isoformat(),'product_id':pid,'quantity':qty,'request_key':key or str(uuid.uuid4())})
 def order(self,token):
  return next(o for o in self.req('/api/admin',admin=True)[1]['orders'] if o['token']==token)
 def test_21_push_auth_and_order_queue(self):
  from test_notifications import subscription
  from unittest.mock import patch
  self.assertEqual(self.req('/api/admin/push')[0],401)
  sub=subscription()
  self.assertEqual(self.req('/api/admin/push/subscribe',{'subscription':sub})[0],401)
  with patch.dict(os.environ,{'BAKERY_PUSH_SUBJECT':'mailto:owner@example.org'}):
   config=self.req('/api/admin/push',admin=True)[1]
   self.assertTrue(config['configured']);self.assertNotIn('private',json.dumps(config))
   self.assertEqual(self.req('/api/admin/push/subscribe',{'subscription':sub},admin=True)[0],200)
   pid=self.new_product();key=str(uuid.uuid4());first=self.reserve(pid,key=key);self.reserve(pid,key=key)
   with server.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM push_jobs WHERE order_id=?',(first[1]['id'],)).fetchone()[0],1)
   self.assertEqual(self.req('/api/admin/push/test',{'subscription':sub},admin=True)[0],200)
   self.assertEqual(self.req('/api/admin/push/disable',{'subscription':sub},admin=True)[0],200)
   self.assertFalse(self.req('/api/admin/push/status',{'subscription':sub},admin=True)[1]['active'])
 def test_22_named_photo_import_idempotent(self):
  import import_photos
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as folder:
   Image.new('RGB',(30,30)).save(Path(folder)/'Тестова назва торта.jpg')
   with server.connect() as c:before=c.execute('SELECT COUNT(*) FROM gallery').fetchone()[0]
   self.assertEqual(import_photos.import_named(folder),1);self.assertEqual(import_photos.import_named(folder),0)
   with server.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM gallery').fetchone()[0],before+1)
 def test_01_public_privacy(self):
  self.assertEqual(self.req('/api/admin')[0],401)
  d=self.req('/api/public')[1];self.assertNotIn('orders',d);self.assertEqual(len(d['gallery']),8)
  self.assertEqual(sum(len(g['photos']) for g in d['gallery']),10)
 def test_02_concurrent_no_oversell(self):
  pid=self.new_product(3)
  with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(lambda _:self.reserve(pid),range(8)))
  self.assertEqual(sum(r[0]==200 for r in results),3);self.assertEqual(sum(r[0]==409 for r in results),5)
  with server.connect() as c:self.assertEqual(c.execute('SELECT stock FROM products WHERE id=?',(pid,)).fetchone()[0],0)
 def test_03_duplicate_retry(self):
  pid=self.new_product();key=str(uuid.uuid4());a=self.reserve(pid,key=key);b=self.reserve(pid,key=key)
  self.assertEqual(a[1]['token'],b[1]['token'])
  with server.connect() as c:self.assertEqual(c.execute('SELECT stock FROM products WHERE id=?',(pid,)).fetchone()[0],2)
 def test_04_cancel_once(self):
  pid=self.new_product();token=self.reserve(pid,2)[1]['token'];o=self.order(token)
  for _ in range(2):self.assertEqual(self.req('/api/admin/order',{**o,'status':'Скасовано'},True)[0],200)
  with server.connect() as c:self.assertEqual(c.execute('SELECT stock FROM products WHERE id=?',(pid,)).fetchone()[0],3)
  self.assertEqual(self.req('/api/admin/order',{**o,'status':'Підтверджено'},True)[0],400)
 def test_05_validation(self):
  pid=self.new_product();self.assertEqual(self.reserve(pid,0)[0],400);self.assertEqual(self.reserve(pid,4)[0],409)
  self.assertEqual(self.req('/api/custom',{'name':'A','phone':'wrong','request_key':str(uuid.uuid4())})[0],400)
 def test_06_custom_and_private_photos(self):
  out=io.BytesIO();Image.new('RGB',(40,40),'pink').save(out,format='PNG')
  upload=self.req('/api/upload',{'data':base64.b64encode(out.getvalue()).decode(),'public':False})
  self.assertEqual(upload[0],200);url=upload[1]['url'];self.assertEqual(self.req(url)[0],404);self.assertEqual(self.req(url,admin=True)[0],200)
  body={'name':'Ірина','phone':'+380671234567','date':server.datetime.now(server.KYIV).date().isoformat(),'occasion':'Свято','guests':12,'budget':200000,'allergies':'Горіхи','sugar_free':True,'photos':[url],'request_key':str(uuid.uuid4()),'inspiration':1}
  r=self.req('/api/custom',body);self.assertEqual(r[0],200);o=self.order(r[1]['token']);self.assertEqual(o['status'],'Нове');self.assertEqual(o['details']['allergies'],'Горіхи')
  public=self.req('/api/status',{'token':r[1]['token']})[1];self.assertNotIn('phone',public);self.assertNotIn('name',public)
 def test_07_completed_and_costs_persist(self):
  token=self.reserve(self.new_product())[1]['token'];o=self.order(token)
  self.assertEqual(self.req('/api/admin/order',{**o,'status':'Виконано','total':30000,'direct_cost':12000,'other_cost':3000},True)[0],200)
  with server.connect() as c:
   row=c.execute('SELECT * FROM orders WHERE id=?',(o['id'],)).fetchone();self.assertTrue(row['completed']);self.assertEqual(row['total']-row['direct_cost']-row['other_cost'],15000)
  self.assertEqual(self.req('/api/admin/expense',{'description':'Коробки','amount':5000,'date':server.datetime.now(server.KYIV).date().isoformat()},True)[0],200)
 def test_08_csrf(self):
  self.assertEqual(self.req('/api/admin/settings',{},True,{'Origin':'https://evil.example'})[0],403)
  self.assertEqual(self.req('/api/admin/settings',{},True,{'X-Bakery-Request':'0'})[0],403)
 def test_09_bad_upload(self):
  self.assertEqual(self.req('/api/upload',{'data':base64.b64encode(b'<svg onload="evil"/>').decode()})[0],400)
  self.assertEqual(self.req('/api/upload',{'data':'YQ==','public':True})[0],401)
 def test_10_product_gallery_settings(self):
  gallery=self.req('/api/public')[1]['gallery'];photo=gallery[0]['photos'][0]
  body={'name':'Новий десерт','description':'Опис','photo':photo,'price':12345,'stock':4,'pickup':'За домовленістю','active':True,'demo':False}
  self.assertEqual(self.req('/api/admin/product',body,True)[0],200)
  self.assertEqual(self.req('/api/admin/gallery',{'name':'Нова робота','description':'Опис','category':server.CATEGORIES[0],'photos':[photo]},True)[0],200)
  self.assertEqual(self.req('/api/admin/settings',{'name':'Пекарня','intro':'Привіт','phone':'+380671234567','pickup':'За домовленістю','demo':False},True)[0],200)
 def test_11_invalid_date_and_hidden_product(self):
  pid=self.new_product()
  with server.connect() as c:c.execute('UPDATE products SET active=0 WHERE id=?',(pid,))
  self.assertEqual(self.reserve(pid)[0],409)
  with self.assertRaises(ValueError):server.validdate('2000-01-01',True)
 def test_12_session_logout(self):
  _,_,headers=self.req('/api/login',{'password':'test-password-12345'});cookie=headers['Set-Cookie'].split(';')[0]
  self.assertEqual(self.req('/api/logout',{},extra={'Cookie':cookie})[0],200)
  self.assertEqual(self.req('/api/admin',extra={'Cookie':cookie})[0],401)
 def test_13_private_cost_snapshot_and_override(self):
  photo=self.req('/api/public')[1]['gallery'][0]['photos'][0]
  body={'name':'Десерт із собівартістю','description':'Тест','photo':photo,'price':15000,'stock':10,'pickup':'Узгодимо','active':True,'demo':True,'unit_cost':4025}
  self.assertEqual(self.req('/api/admin/product',body,True)[0],200)
  product=next(p for p in self.req('/api/admin',admin=True)[1]['products'] if p['name']==body['name']);pid=product['id']
  self.assertEqual(product['unit_cost'],4025)
  public=next(p for p in self.req('/api/public')[1]['products'] if p['id']==pid)
  self.assertNotIn('unit_cost',public)
  first=self.reserve(pid,3)[1]['token'];order=self.order(first)
  self.assertEqual(order['direct_cost'],12075);self.assertEqual(order['unit_cost'],4025)
  self.assertNotIn('unit_cost',self.req('/api/status',{'token':first})[1])
  self.assertEqual(self.req('/api/admin/product',{**body,'id':pid,'unit_cost':5000,'stock':7},True)[0],200)
  self.assertEqual(self.order(first)['direct_cost'],12075)
  second=self.reserve(pid,2)[1]['token'];self.assertEqual(self.order(second)['direct_cost'],10000)
  self.assertEqual(self.req('/api/admin/order',{**order,'status':'Виконано'},True)[0],200)
  self.assertEqual(self.order(first)['direct_cost'],12075)
  self.assertEqual(self.req('/api/admin/order',{**order,'status':'Виконано','direct_cost':13000},True)[0],200)
  self.assertEqual(self.order(first)['direct_cost'],13000)
  self.assertEqual(next(p for p in self.req('/api/admin',admin=True)[1]['products'] if p['id']==pid)['unit_cost'],5000)
  for invalid in (-1,1.5,'40',True):
   self.assertEqual(self.req('/api/admin/product',{**body,'unit_cost':invalid},True)[0],400)
 def test_14_unset_and_zero_cost(self):
  pid=self.new_product();token=self.reserve(pid)[1]['token']
  self.assertIsNone(self.order(token)['unit_cost']);self.assertEqual(self.order(token)['direct_cost'],0)
  with server.connect() as c:c.execute('UPDATE products SET unit_cost=0 WHERE id=?',(pid,))
  token=self.reserve(pid)[1]['token'];self.assertEqual(self.order(token)['unit_cost'],0)
 def test_15_existing_database_migration(self):
  original=server.DB
  try:
   server.DB=Path(TEMP.name)/'migration.sqlite3'
   import sqlite3
   with sqlite3.connect(original) as src, sqlite3.connect(server.DB) as dest:src.backup(dest)
   with server.connect() as c:
    count=c.execute('SELECT count(*) FROM orders').fetchone()[0]
    costs=[tuple(r) for r in c.execute('SELECT id,direct_cost,details FROM orders ORDER BY id')]
    c.execute('ALTER TABLE products DROP COLUMN unit_cost');c.execute('ALTER TABLE orders DROP COLUMN unit_cost');c.execute('ALTER TABLE products DROP COLUMN ingredients_allergens')
   server.init();server.init()
   with server.connect() as c:
    self.assertEqual(c.execute('SELECT count(*) FROM orders').fetchone()[0],count)
    self.assertEqual([tuple(r) for r in c.execute('SELECT id,direct_cost,details FROM orders ORDER BY id')],costs)
    self.assertTrue(all(r[0] is None for r in c.execute('SELECT unit_cost FROM products')))
    self.assertTrue(all(r[0]=='' for r in c.execute('SELECT ingredients_allergens FROM products')))
  finally:server.DB=original
 def test_16_owner_ingredients_and_reservation_snapshot(self):
  photo=self.req('/api/public')[1]['gallery'][0]['photos'][0]
  body={'name':'Партія для тесту','description':'Приклад','photo':photo,'price':15000,'stock':5,'pickup':'Узгодимо','active':True,'demo':True,'ingredients_allergens':'Тестові дані складу\nТестові дані алергенів'}
  self.assertEqual(self.req('/api/admin/product',body)[0],401)
  self.assertEqual(self.req('/api/admin/product',body,True)[0],200)
  product=next(p for p in self.req('/api/public')[1]['products'] if p['name']==body['name'])
  self.assertEqual(product['ingredients_allergens'],body['ingredients_allergens'])
  token=self.reserve(product['id'])[1]['token'];order=self.order(token)
  self.assertNotIn('allergies',order['details']);self.assertEqual(order['details']['ingredients_allergens'],body['ingredients_allergens'])
  self.assertEqual(self.req('/api/admin/product',{**body,'id':product['id'],'stock':4,'ingredients_allergens':'Оновлена інформація'},True)[0],200)
  self.assertEqual(self.order(token)['details']['ingredients_allergens'],body['ingredients_allergens'])
  self.assertEqual(self.req('/api/admin/product',{**body,'ingredients_allergens':'x'*3001},True)[0],400)
 def test_17_ready_product_rejects_customization(self):
  pid=self.new_product()
  body={'name':'Тест','phone':'+380000000000','product_id':pid,'quantity':1,'date':server.datetime.now(server.KYIV).date().isoformat(),'request_key':str(uuid.uuid4())}
  for field,value in [('allergies','Побажання'),('diet','Побажання'),('sugar_free',True)]:
   self.assertEqual(self.req('/api/reserve',{**body,field:value})[0],400)
  with server.connect() as c:self.assertEqual(c.execute('SELECT stock FROM products WHERE id=?',(pid,)).fetchone()[0],3)
  self.assertEqual(self.req('/api/reserve',body)[0],200)
 def test_18_custom_product_inspiration_without_stock_change(self):
  pid=self.new_product(0)
  body={'name':'Тест','phone':'+380000000000','date':server.datetime.now(server.KYIV).date().isoformat(),'occasion':'Свято','guests':8,'budget':100000,'allergies':'Потребує перевірки','diet':'Особливі побажання','sugar_free':True,'photos':[],'product_inspiration':pid,'request_key':str(uuid.uuid4())}
  result=self.req('/api/custom',body);self.assertEqual(result[0],200)
  order=self.order(result[1]['token']);self.assertEqual(order['kind'],'custom');self.assertIsNone(order['product_id']);self.assertEqual(order['status'],'Нове')
  self.assertEqual(order['details']['inspiration']['id'],pid);self.assertEqual(order['details']['inspiration']['type'],'product');self.assertEqual(order['details']['allergies'],body['allergies']);self.assertTrue(order['details']['sugar_free'])
  with server.connect() as c:
   self.assertEqual(c.execute('SELECT stock FROM products WHERE id=?',(pid,)).fetchone()[0],0)
   c.execute('UPDATE products SET active=0 WHERE id=?',(pid,))
  self.assertEqual(self.req('/api/custom',{**body,'request_key':str(uuid.uuid4())})[0],400)
 def test_19_email_retired_tracking_kept(self):
  for route in ['/api/recovery/request','/api/recovery/verify']:
   self.assertEqual(self.req(route,{'email':'test@example.com','token':'old-token'})[0],404)
  with server.connect() as c:c.execute('DELETE FROM limits')
  result=self.reserve(self.new_product())[1]
  self.assertNotIn('email_state',result)
  self.assertEqual(self.req('/api/status',{'token':result['token']})[0],200)
  self.assertNotIn('email_state',self.req('/api/status',{'token':result['token']})[1])
  self.assertEqual(self.req('/recover')[0],200)
 def test_20_retire_pending_emails_preserving_orders(self):
  original=server.DB
  try:
   server.DB=Path(TEMP.name)/'retire-email.sqlite3'
   import sqlite3
   with sqlite3.connect(original) as src, sqlite3.connect(server.DB) as dst:src.backup(dst)
   with server.connect() as c:
    orders=[tuple(r) for r in c.execute('SELECT id,token,status,details,total,direct_cost FROM orders ORDER BY id')]
    c.execute('CREATE TABLE IF NOT EXISTS email_jobs (status TEXT,payload TEXT)')
    c.execute("INSERT INTO email_jobs(status,payload) VALUES('queued','private-message')")
    c.execute('CREATE TABLE IF NOT EXISTS recovery_tokens (hash TEXT)')
    c.execute("INSERT INTO recovery_tokens VALUES('old-verification')")
   server.init();server.init()
   with server.connect() as c:
    self.assertEqual([tuple(r) for r in c.execute('SELECT id,token,status,details,total,direct_cost FROM orders ORDER BY id')],orders)
    self.assertEqual(c.execute('SELECT COUNT(*) FROM recovery_tokens').fetchone()[0],0)
    self.assertEqual(c.execute('SELECT status,payload FROM email_jobs').fetchone()[:],('retired',None))
  finally:server.DB=original
if __name__=='__main__':unittest.main(verbosity=2)
