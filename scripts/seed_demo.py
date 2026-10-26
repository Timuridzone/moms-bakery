"""Create a separate, reproducible portfolio demo. Never touches the working bakery data."""
import os, sys, json, secrets
from pathlib import Path
from datetime import datetime, timedelta
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['BAKERY_DATA']=str(ROOT/'data'/'portfolio-demo')
import server
import import_photos

def seed():
 server.init();import_photos.import_named(ROOT/'pics')
 with server.connect() as c:
  if c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]:
   print('Demo already populated; existing records preserved.');return
  today=datetime.now(server.KYIV).date()
  for name,price,cost,stock in [('Рулет «Вишня-шоколад»',48000,18000,4),('Рулет «Мак-абрикос»',45000,16000,3),('Сети з трьох видів рулетів',60000,22000,5)]:
   g=c.execute('SELECT * FROM gallery WHERE name=?',(name,)).fetchone()
   c.execute('INSERT INTO products(name,description,photo,price,stock,pickup,unit_cost,demo) VALUES(?,?,?,?,?,?,?,1)',(name,'Приклад пропозиції. Ціну, склад і наявність потрібно підтвердити.',json.loads(g['photos'])[0],price,stock,'Самовивіз за домовленістю',cost))
  for i,(total,direct,status) in enumerate([(180000,65000,'Виконано'),(240000,80000,'Виконано'),(120000,45000,'Виконано'),(320000,105000,'Виконано'),(96000,36000,'Виконано'),(180000,60000,'Нове'),(220000,75000,'Підтверджено'),(150000,50000,'Готово')],1):
   pickup=today+timedelta(days=i-5) if i>5 else today.replace(day=min(i*3,today.day))
   details={'occasion':['Сімейне свято','День народження','Зустріч із друзями'][i%3],'date':pickup.isoformat(),'guests':12,'budget':total,'photos':[],'comments':'Демонстраційне замовлення для портфоліо.','allergies':'','diet':'','flavours':''}
   created=today.isoformat()+'T10:00:00+03:00'
   c.execute('INSERT INTO orders(token,request_key,kind,name,phone,details,total,direct_cost,other_cost,status,created,completed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(secrets.token_urlsafe(32),secrets.token_hex(32),'custom',f'Демо-клієнт {i}','+380000000000',json.dumps(details,ensure_ascii=False),total,direct,5000,status,created,created if status=='Виконано' else None))
  c.execute('INSERT INTO expenses(description,amount,date) VALUES(?,?,?)',('Пакування — демонстраційна витрата',32000,today.isoformat()))
 server.set_password('portfolio-demo-local')
 print('Fictional demo ready. Local-only password: portfolio-demo-local')

if __name__=='__main__':seed()
