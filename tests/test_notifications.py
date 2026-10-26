import base64,json,os,sqlite3,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import notifications as n

def subscription(endpoint='https://fcm.googleapis.com/fcm/send/test'):
 key=ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
 enc=lambda v:base64.urlsafe_b64encode(v).decode().rstrip('=')
 return {'endpoint':endpoint,'keys':{'p256dh':enc(key),'auth':enc(b'1'*16)}}

class PushTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.data=Path(self.temp.name)
  self.env=patch.dict(os.environ,{'BAKERY_PUSH_SUBJECT':'mailto:owner@example.org'});self.env.start()
  with self.connect() as c:
   c.execute('CREATE TABLE orders(id INTEGER PRIMARY KEY)');c.execute('INSERT INTO orders VALUES(1)');n.init(c)
   c.execute('INSERT INTO push_subscriptions(endpoint,subscription) VALUES(?,?)',('https://fcm.googleapis.com/fcm/send/test',json.dumps(subscription())))
 def tearDown(self):self.env.stop();self.temp.cleanup()
 def connect(self):
  c=sqlite3.connect(self.data/'test.sqlite3',isolation_level=None);c.row_factory=sqlite3.Row;return c
 def test_keys_persist_private(self):
  key=n.public_key(self.data);self.assertEqual(key,n.public_key(self.data));self.assertEqual((self.data/'push-private.pem').stat().st_mode&0o777,0o600)
 def test_validation_blocks_local_and_invalid_keys(self):
  for url in ['http://fcm.googleapis.com/a','https://127.0.0.1/a','https://fcm.googleapis.com.evil.example/a','https://fcm.googleapis.com@evil.example/a','https://fcm.googleapis.com:444/a']:
   with self.assertRaises(ValueError):n.validate_subscription(subscription(url))
  self.assertEqual(n.validate_subscription(subscription())['keys'].keys(),{'p256dh','auth'})
  bad=subscription();bad['keys']['p256dh']='a'*87
  with self.assertRaises(ValueError):n.validate_subscription(bad)
 def test_unique_jobs_and_no_private_payload(self):
  with self.connect() as c:n.enqueue(c,1);n.enqueue(c,1)
  sent=[]
  n.deliver_one(self.connect,self.data,lambda **kw:sent.append(kw))
  self.assertFalse(n.deliver_one(self.connect,self.data,lambda **kw:None));self.assertEqual(len(sent),1)
  payload=json.loads(sent[0]['data']);self.assertEqual(payload['url'],'/admin#order-1');self.assertEqual(set(payload),{'title','body','url','tag'})
  self.assertEqual(sent[0]['vapid_claims'],{'sub':'mailto:owner@example.org'})
 def test_retry_and_expired_subscription(self):
  with self.connect() as c:n.enqueue(c,1)
  def fail(**kw):raise OSError('offline')
  n.deliver_one(self.connect,self.data,fail)
  with self.connect() as c:
   row=c.execute('SELECT * FROM push_jobs').fetchone();self.assertEqual(row['status'],'pending');self.assertEqual(row['attempts'],1);self.assertGreater(row['next_attempt'],time.time());c.execute('UPDATE push_jobs SET next_attempt=0')
  class Gone(Exception):response=type('Response',(),{'status_code':410})()
  def gone(**kw):raise Gone()
  n.deliver_one(self.connect,self.data,gone)
  with self.connect() as c:
   self.assertEqual(c.execute('SELECT active FROM push_subscriptions').fetchone()[0],0);self.assertEqual(c.execute('SELECT status FROM push_jobs').fetchone()[0],'failed')
 def test_transaction_rollback_and_disabled(self):
  with self.connect() as c:
   c.execute('BEGIN');n.enqueue(c,1);c.rollback();self.assertEqual(c.execute('SELECT COUNT(*) FROM push_jobs').fetchone()[0],0)
   c.execute('UPDATE push_subscriptions SET active=0');n.enqueue(c,1);self.assertEqual(c.execute('SELECT COUNT(*) FROM push_jobs').fetchone()[0],0)
 def test_abandoned_lease_and_bounded_retry(self):
  with self.connect() as c:
   n.enqueue(c,1);c.execute("UPDATE push_jobs SET status='sending',next_attempt=0,attempts=5")
  n.deliver_one(self.connect,self.data,lambda **kw:self.fail('Should not send'))
  with self.connect() as c:self.assertEqual(c.execute('SELECT status FROM push_jobs').fetchone()[0],'expired')
 def test_real_encryption_and_vapid_with_mock_transport(self):
  import requests
  with self.connect() as c:n.enqueue(c,1)
  response=requests.Response();response.status_code=201
  with patch('requests.Session.request',return_value=response) as send:
   n.deliver_one(self.connect,self.data)
   self.assertEqual(send.call_count,1)
   self.assertIn('vapid',send.call_args.kwargs['headers']['authorization'])
   self.assertIsInstance(send.call_args.kwargs['data'],bytes)
  with self.connect() as c:self.assertEqual(c.execute('SELECT status FROM push_jobs').fetchone()[0],'sent')
 def test_redirects_disabled(self):
  with patch('requests.Session.request') as send:
   n.NoRedirectSession().post('https://fcm.googleapis.com/a');self.assertIs(send.call_args.kwargs['allow_redirects'],False)

if __name__=='__main__':unittest.main()
