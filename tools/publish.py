"""Publish only authenticated, compiled update assets. No cross-repo credentials."""
import base64,hashlib,io,json,os,re,urllib.request,urllib.error,urllib.parse,zipfile
from pathlib import Path
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes

repo=os.environ.get('GH_REPO','makspruga-design/Lumira-updates')
assert repo=='makspruga-design/Lumira-updates'
doc=json.loads(Path('release/payload.json').read_text())
assert re.fullmatch(r'\d+\.\d+\.\d+',doc['version'])
tag='v'+doc['version'];archive=base64.b64decode(doc['archive_base64'],validate=True)
assert len(archive)<64*1024*1024 and hashlib.sha256(archive).hexdigest()==doc['archive_sha256']
key=load_pem_public_key(Path('update-public.pem').read_bytes())
assets={};manifests=[];notes='';code=None
with zipfile.ZipFile(io.BytesIO(archive)) as z:
 names=z.namelist();assert len(names)==len(set(names)) and len(names)<=4098
 for platform in ['android','windows']:
  filename='lumira-017-'+platform+'.json';raw=z.read(filename);assert len(raw)<=1048576
  env=json.loads(raw);payload=base64.b64decode(env['payload'],validate=True)
  key.verify(base64.b64decode(env['signature'],validate=True),payload,padding.PKCS1v15(),hashes.SHA256())
  m=json.loads(payload)
  assert m['protocol']==1 and m['native_id']=='lumira-017' and m['platform']==platform and m['engine']=='4.3'
  assert m['version']==doc['version'] and type(m['version_code']) is int and m['version_code']>23
  assert code in [None,m['version_code']];code=m['version_code'];notes=m['notes']
  assert len(m['packs'])<=2048
  assets[filename]=raw;manifests.append(filename)
  for p in m['packs']:
   digest=p['sha256'];assert re.fullmatch('[a-f0-9]{64}',digest)
   assert p['url'].startswith('https://github.com/'+repo+'/releases/download/') and p['url'].endswith('/'+digest+'.zip')
   name=digest+'.zip'
   if name not in names:
    # Reused immutable resources retain the URL of their earlier release.
    assert not p['url'].startswith('https://github.com/'+repo+'/releases/download/'+tag+'/')
    with urllib.request.urlopen(p['url'],timeout=60) as r:b=r.read(268435457)
   else:b=z.read(name)
   assert len(b)==p['size'] and len(b)<=268435456 and hashlib.sha256(b).hexdigest()==digest
   with zipfile.ZipFile(io.BytesIO(b)) as pack:
    entries=pack.namelist();assert len(entries)==1 and pack.testzip() is None
    resource=entries[0]
    assert not resource.startswith('/') and '..' not in resource.split('/')
    assert resource not in ['project.binary','.godot/global_script_class_cache.cfg','scripts/updates/update_bootstrap.gdc','scripts/updates/update_bootstrap.gd.remap']
    assert not resource.startswith(('.ci/','.github/','docs/','tools/','server/','build/')) and not resource.endswith(('.gd','.py','.js','.b64','.keystore'))
   if name in names:assets[name]=b
 assert set(names)==set(assets), 'Unsigned or unrelated files must not enter the public channel'
print('Verified signed manifests and',len(assets)-2,'compiled resource files;',sum(map(len,assets.values())),'bytes')
if os.environ.get('LUMIRA_VERIFY_ONLY')=='1':raise SystemExit(0)
token=os.environ['GH_TOKEN'];api='https://api.github.com/repos/'+repo
def request(method,url,data=None,binary=False):
 body=data if binary else json.dumps(data).encode() if data is not None else None
 req=urllib.request.Request(url,data=body,method=method,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/octet-stream' if binary else 'application/json'})
 with urllib.request.urlopen(req,timeout=90) as response:return json.load(response)
try:existing=request('GET',api+'/releases/tags/'+tag)
except urllib.error.HTTPError as error:
 if error.code!=404:raise
 existing=None
if existing and not existing['draft']:
 byname={a['name']:a for a in existing['assets']}
 for filename in manifests:
  with urllib.request.urlopen(byname[filename]['browser_download_url'],timeout=60) as r:assert r.read()==assets[filename], 'Published release is immutable'
 print('Release already published; verified immutable manifests');raise SystemExit(0)
release=existing or request('POST',api+'/releases',{'tag_name':tag,'target_commitish':'main','name':'Lumira '+doc['version'],'body':notes,'draft':True})
upload=release['upload_url'].split('{')[0];already={a['name']:a for a in release.get('assets',[])}
for name,b in sorted(assets.items()):
 if name in already:
  # A retried draft must contain exactly the same bytes; never overwrite it.
  assert already[name]['size']==len(b)
  result=request('GET',already[name]['url'])
  assert result.get('digest')=='sha256:'+hashlib.sha256(b).hexdigest()
  continue
 request('POST',upload+'?name='+urllib.parse.quote(name),b,True)
request('PATCH',api+'/releases/'+str(release['id']),{'draft':False,'make_latest':'true'})
print('PUBLISHED',tag,'with',len(assets),'verified assets')
