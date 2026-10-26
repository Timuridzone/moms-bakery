"""Import named originals once; leave stock, pricing and ingredients untouched."""
import hashlib,json,re,secrets,time,unicodedata
from pathlib import Path
from PIL import Image,ImageOps
import server

def import_named(folder):
 imported=0
 with server.connect() as c:
  c.execute('CREATE TABLE IF NOT EXISTS photo_imports(digest TEXT PRIMARY KEY, gallery_id INTEGER NOT NULL REFERENCES gallery(id))')
  for path in sorted(Path(folder).iterdir()):
   if path.suffix.lower() not in ('.jpg','.jpeg','.png','.webp') or path.name.startswith('photo_'): continue
   digest=hashlib.sha256(path.read_bytes()).hexdigest()
   if c.execute('SELECT 1 FROM photo_imports WHERE digest=?',(digest,)).fetchone(): continue
   name=re.sub(r'(?<=рулетів)\d+$','',unicodedata.normalize('NFC',path.stem))
   if 'Лісова казка' in name: name='Торт «Лісова казка»'
   # This supplied photograph shows the same forest cake as the original gallery.
   previous=c.execute("SELECT id FROM gallery WHERE name='Торт із лісовим декором'").fetchone() if 'Лісова казка' in name else None
   if not previous: previous=c.execute('SELECT id FROM gallery WHERE name=?',(name,)).fetchone()
   pid=secrets.token_hex(16)+'.jpg'
   with Image.open(path) as source:
    if source.width*source.height>25000000: raise ValueError('Фото завелике: '+path.name)
    im=ImageOps.exif_transpose(source).convert('RGB');im.thumbnail((1600,1600));im.save(server.DATA/'uploads'/pid,quality=87)
   c.execute('BEGIN IMMEDIATE')
   c.execute('INSERT INTO photos VALUES(?,1,?)',(pid,time.time()))
   if previous:
    gid=previous['id']
    photos=json.loads(c.execute('SELECT photos FROM gallery WHERE id=?',(gid,)).fetchone()[0])
    if '/media/'+pid not in photos: photos.append('/media/'+pid)
    c.execute('UPDATE gallery SET name=?,photos=? WHERE id=?',(name,json.dumps(photos[:4]),gid))
   else:
    category='Закуски' if 'паштет' in name.lower() else 'Великодня випічка' if 'Пасха' in name else 'Домашні десерти' if any(s in name.lower() for s in ('рулет','кекс')) else 'Святкові торти'
    gid=c.execute('INSERT INTO gallery(name,category,description,photos) VALUES(?,?,?,?)',(name,category,'Склад, начинку та можливість повторення уточнюємо під час замовлення.',json.dumps(['/media/'+pid]))).lastrowid
   c.execute('INSERT INTO photo_imports VALUES(?,?)',(digest,gid));c.commit();imported+=1
 return imported

if __name__=='__main__':
 server.init(); print(f'Додано або оновлено робіт: {import_named(server.ROOT/"pics")}')
