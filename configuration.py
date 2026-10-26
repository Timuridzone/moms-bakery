"""Load server-only local configuration without executing shell code."""
import os, re, shlex

def load_env(path):
 if not path.exists(): return
 for line in path.read_text().splitlines():
  line=line.strip()
  if not line or line.startswith('#'): continue
  key,sep,value=line.partition('=')
  if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*',key): raise ValueError('Некоректний формат .env')
  parts=shlex.split(value,comments=True)
  if len(parts)>1: raise ValueError('Візьміть значення .env з пробілами в лапки.')
  os.environ.setdefault(key,parts[0] if parts else '')

