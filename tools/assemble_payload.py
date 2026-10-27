"""Reassemble a chunked signed candidate before the existing public verifier."""
import hashlib,json
from pathlib import Path
index=Path('release/payload.parts.json')
if index.exists():
 doc=json.loads(index.read_text());chunks=[]
 assert 0<doc['size']<90*1024*1024
 assert 0<len(doc['parts'])<=100
 for part in doc['parts']:
  path=Path(part['path'])
  assert path.parts[:2]==('release','visual-01966') and '..' not in path.parts
  raw=path.read_bytes()
  assert len(raw)==part['size'] and hashlib.sha256(raw).hexdigest()==part['sha256']
  chunks.append(raw)
 data=b''.join(chunks)
 assert len(data)==doc['size'] and hashlib.sha256(data).hexdigest()==doc['sha256']
 assert json.loads(data)['version']==doc['version']
 Path('release/payload.json').write_bytes(data)
 print('Reassembled and SHA256 verified',doc['version'])
