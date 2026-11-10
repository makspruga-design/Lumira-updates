#!/usr/bin/env python3
"""Pinned .71 delivery using the existing publisher; no signing keys."""
import argparse,base64,hashlib,io,json,os,re,time,urllib.request,urllib.error,zipfile
from pathlib import Path
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
REPO='makspruga-design/Lumira-updates'
API='https://api.github.com/repos/'+REPO
PREFIX='https://github.com/'+REPO+'/releases/download/'
PIN='7a19810298c5b9639cf99dd845bb76fdc127e3c4'
SOURCE='af81b027961b99dd47d004319de193216df4935e'
ROWS=[('stage','Lumeria-0.19.71-QA-resources.zip',57908170,'03e0eca653e535cbbb98cd4dcc8d2215260e1c0c92f63238b4b7918b005f8e18'),('final','Lumeria-0.19.71-QA-final.zip',37544413,'c180854935d84646e9170a26f375bd93776e00b9f791e060a9d7a8a5fc2fa727')]
PUBLISHER='13d3ecb09631fdea39398dfc4484056bb0ca2b3c548609402e090750ff034904'
KEY='99b858157199c870842c28a99461bbae89b857fe7788e7218cd3f774db70bad4'
def sha(b):return hashlib.sha256(b).hexdigest()
def get(url,limit):
 with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Lumeria-0.19.71-verifier'}),timeout=90) as r:b=r.read(limit+1)
 assert len(b)<=limit;return b
def api(path,body=None):
 headers={'Accept':'application/vnd.github+json','User-Agent':'Lumeria-0.19.71-verifier'}
 if os.environ.get('GH_TOKEN'):headers['Authorization']='Bearer '+os.environ['GH_TOKEN']
 data=None if body is None else json.dumps(body).encode()
 if body is not None:headers['Content-Type']='application/json'
 with urllib.request.urlopen(urllib.request.Request(API+path,data=data,headers=headers,method='GET' if body is None else 'PATCH'),timeout=90) as r:return json.load(r)
def envelope(raw,platform):
 e=json.loads(raw);b=base64.b64decode(e['payload'],validate=True);load_pem_public_key(Path('update-public.pem').read_bytes()).verify(base64.b64decode(e['signature'],validate=True),b,padding.PKCS1v15(),hashes.SHA256());d=json.loads(b)
 assert d['protocol']==1 and d['native_id']=='lumira-017' and d['engine']=='4.3' and d['platform']==platform
 assert d['version']=='0.19.71' and d['version_code']==108
 return d
def inspect(b,role):
 expected=194 if role=='stage' else 738
 with zipfile.ZipFile(io.BytesIO(b)) as z:
  assert z.testzip() is None and len(z.namelist())==expected+2 and len(set(z.namelist()))==len(z.namelist())
  assert sum(i.file_size for i in z.infolist())<64*1024*1024
  docs={p:envelope(z.read('lumira-017-'+p+'.json'),p) for p in ('android','windows')}
  assert docs['android']['packs']==docs['windows']['packs']
  if role=='final':assert len(docs['windows']['packs'])==1487 and sum(i['size'] for i in docs['windows']['packs'])==188356129
  entries={i['sha256']+'.zip':i for i in docs['windows']['packs']}
  for n in z.namelist():
   if n.startswith('lumira-017-'):continue
   assert re.fullmatch('[0-9a-f]{64}\\.zip',n) and n in entries
   raw=z.read(n);item=entries[n];assert sha(raw)==item['sha256'] and len(raw)==item['size']
   with zipfile.ZipFile(io.BytesIO(raw)) as pack:
    assert pack.namelist()==[item['resource']] and pack.testzip() is None
    assert not item['resource'].startswith(('.ci/','.github/','docs/','tools/','server/'))
 return docs
def prepare(local=None):
 assert sha(Path('tools/publish.py').read_bytes())==PUBLISHER and sha(Path('update-public.pem').read_bytes())==KEY
 for role,name,size,digest in ROWS:
  b=(local/name).read_bytes() if local else get('https://raw.githubusercontent.com/'+REPO+'/'+PIN+'/'+name,64*1024*1024)
  assert len(b)==size and sha(b)==digest;inspect(b,role)
  target=Path('release/resources-01971/payload.json' if role=='stage' else 'release/payload.json');target.parent.mkdir(parents=True,exist_ok=True)
  wrapper={'version':'0.19.71','source_commit':SOURCE,'source_run':'local-godot43-integration-native-01970'+('-resource-stage' if role=='stage' else ''),'archive_sha256':digest,'archive_base64':base64.b64encode(b).decode()}
  target.write_text(json.dumps(wrapper,separators=(',',':')))
 print('Pinned ZIP SHA/CRC, RSA, identities and closure verified; existing publisher/key unchanged',flush=True)
def verify():
 results=[];manifests={};documents={}
 for role,name,size,digest in ROWS:
  path=Path('release/resources-01971/payload.json' if role=='stage' else 'release/payload.json');wrapper=json.loads(path.read_text());b=base64.b64decode(wrapper['archive_base64'],validate=True);assert len(b)==size and sha(b)==digest;docs=inspect(b,role)
  tag='v0.19.71-resources' if role=='stage' else 'v0.19.71';rel=api('/releases/tags/'+tag)
  assert rel['draft'] is False and rel['prerelease'] is (role=='stage')
  remote={}
  for page in range(1,23):
   entries=api('/releases/'+str(rel['id'])+'/assets?per_page=100&page='+str(page))
   for e in entries:assert e['name'] not in remote;remote[e['name']]=e
   if len(entries)<100:break
  else:raise AssertionError('asset pagination limit')
  with zipfile.ZipFile(io.BytesIO(b)) as z:
   names=[n for n in z.namelist() if role=='final' or not n.startswith('lumira-017-')]
   assert set(remote)==set(names)
   for n in names:
    expected=z.read(n);e=remote[n];assert e['size']==len(expected) and e['digest']=='sha256:'+sha(expected)
    actual=get(PREFIX+tag+'/'+n,268435456);assert actual==expected
    if n.endswith('.zip'):
     with zipfile.ZipFile(io.BytesIO(actual)) as p:assert p.testzip() is None
    else:manifests[n]=actual
   results.append({'tag':tag,'assets':len(names),'all_downloaded_sha256_crc_verified':True})
  if role=='final':documents=docs
 latest=api('/releases/latest');assert latest['tag_name']=='v0.19.71' and not latest['prerelease']
 for n,raw in manifests.items():
  for attempt in range(4):
   actual=get('https://github.com/'+REPO+'/releases/latest/download/'+n,1048576)
   if actual==raw:break
   if attempt==3:raise AssertionError('Latest route stale: '+n)
   time.sleep(2**attempt)
  platform='android' if 'android' in n else 'windows';envelope(actual,platform)
 # Explain test-release scope; no manifest bytes or signature is changed.
 note='\n\nTest release. Windows requires native017 updater repair-r1; the original Windows ZIP loader is not compatible with this cumulative pack count. Exact repair-r1 Windows GL upgrade/rollback/restart passed. Android exact-base/device gameplay has not been tested; this is an owner-requested test update. Web is not part of this release. Back up saves before manual testing.'
 api('/releases/'+str(latest['id']),{'body':documents['windows']['notes']+note})
 receipt={'status':'verified_published','version':'0.19.71','code':108,'source_commit':SOURCE,'releases':results,'latest_release_id':latest['id'],'latest_routes_exact':True,'cumulative_packs':1487,'cumulative_zip_bytes':188356129,'android_device_test':'NOT_RUN','windows_requirement':'repair-r1'}
 Path('publication-01971.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','verify']);p.add_argument('--local-archives',type=Path);a=p.parse_args()
 prepare(a.local_archives) if a.mode=='prepare' else verify()
