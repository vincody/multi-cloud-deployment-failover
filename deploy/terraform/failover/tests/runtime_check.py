"""Run from repository root. Requires Docker, PyYAML and dcs29-issues:push-check.

Real PostgreSQL promotion and app verification; adapts only K3s/systemctl transport.
Creates isolated containers/volumes and removes only those resources in finally.
"""
from pathlib import Path
import yaml, subprocess, time, secrets, json
out=Path('tmp/automation-runtime').resolve();out.mkdir(parents=True,exist_ok=True)
assets=Path('deploy/terraform/automation/templates')
prefix='dcs29-drcheck-'+str(int(time.time()));network=prefix+'-net';volumes=[];containers=[]
def run(args,input=None,check=True):
 r=subprocess.run(['docker',*args],input=input,capture_output=True)
 if check and r.returncode:raise RuntimeError(r.stderr.decode(errors='replace')[:1200])
 return r.stdout.decode(errors='replace').strip()
def volume(suffix):
 n=prefix+'-'+suffix;run(['volume','create','--label','purpose=automation-check',n]);volumes.append(n);return n
def sql(name,q):return run(['exec',name,'psql','-v','ON_ERROR_STOP=1','-U','postgres','-d','issues','-At','-c',q])
def ready(name):
 for _ in range(90):
  try:
   if '10.200.29.' in sql(name,'SHOW listen_addresses'):return
  except RuntimeError:pass
  time.sleep(.5)
 raise RuntimeError('Database not ready')
def api(name,path,body=None):
 code="import urllib.request,json; b="+repr(body)+";r=urllib.request.Request('http://127.0.0.1:8080' + "+repr(path)+",data=json.dumps(b).encode() if b else None,headers={'Content-Type':'application/json'});print(urllib.request.urlopen(r).read().decode())"
 return json.loads(run(['exec',name,'python','-c',code]))
passwords={k:secrets.token_hex(24) for k in ['POSTGRES_PASSWORD','APP_DB_PASSWORD','REPLICATION_PASSWORD']}
passwords.update(POSTGRES_DB='issues',PGDATA='/var/lib/postgresql/data/pgdata')
env=out/'test.env';env.write_text('\n'.join(f'{k}={v}' for k,v in passwords.items()),encoding='utf-8')
for site,ip in [('aws','10.200.29.1'),('azure','10.200.29.2')]:
 cfg=yaml.safe_load((assets/'postgres-config.yaml.tmpl').read_text(encoding='utf-8').replace('__LOCAL_IP__',ip))
 folder=out/site;folder.mkdir(exist_ok=True)
 for k,v in cfg['data'].items():
  # Test-only Docker app subnet; production pg_hba allows the K3s pod CIDR.
  if k=='pg_hba.conf':v+='\nhost issues issues_app 10.200.29.0/24 scram-sha-256\nhost issues postgres 10.200.29.0/24 scram-sha-256\n'
  (folder/k).write_bytes(v.encode())
az=list(yaml.safe_load_all((assets/'postgres-azure.yaml.tmpl').read_text(encoding='utf-8')))
init=next(d for d in az if d['kind']=='StatefulSet')['spec']['template']['spec']['initContainers']
clone=next(i for i in init if i['name']=='base-backup')['args'][0]
try:
 run(['network','create','--label','purpose=automation-check','--subnet','10.200.29.0/24','--gateway','10.200.29.254',network])
 a=volume('aws');z=volume('azure');pa=volume('pass1');pb=volume('pass2')
 primary=prefix+'-primary';containers.append(primary)
 run(['run','-d','--name',primary,'--network',network,'--ip','10.200.29.1','--env-file',str(env),'-v',f'{a}:/var/lib/postgresql/data','-v',f'{out/"aws"}:/etc/postgresql:ro','-v',f'{out/"aws"}:/docker-entrypoint-initdb.d:ro','postgres:17','postgres','-c','config_file=/etc/postgresql/postgresql.conf'])
 ready(primary)
 apps=[]
 for site,ip in [('aws','10.200.29.3'),('azure','10.200.29.4')]:
  name=prefix+'-app-'+site;apps.append(name);containers.append(name)
  run(['run','-d','--name',name,'--network',network,'--ip',ip,'-e',f'DATABASE_URL=postgresql://issues_app:{passwords["APP_DB_PASSWORD"]}@10.200.29.{1 if site=="aws" else 2}:5432/issues','-e','APP_ENV='+site,'dcs29-issues:push-check'])
 run(['exec',apps[0],'python','-c','from database.issues import initialize; initialize()'])
 for passvol in [pa,pb]:
  run(['run','--rm','--network',network,'--ip','10.200.29.2','--env-file',str(env),'-e','SOURCE_DB_HOST=10.200.29.1','-v',f'{z}:/var/lib/postgresql/data','-v',f'{passvol}:/var/lib/postgresql/repl','--entrypoint','bash','postgres:17','-ec',clone])
 standby=prefix+'-standby';containers.append(standby)
 run(['run','-d','--name',standby,'--network',network,'--ip','10.200.29.2','--env-file',str(env),'-v',f'{z}:/var/lib/postgresql/data','-v',f'{pb}:/var/lib/postgresql/repl','-v',f'{out/"azure"}:/etc/postgresql:ro','postgres:17','postgres','-c','config_file=/etc/postgresql/postgresql.conf'])
 ready(standby)
 for _ in range(30):
  try:
   first=api(apps[0],'/api/database/status');second=api(apps[1],'/api/database/status')
   if first['writable'] and second['role']=='standby' and second['replication_connected']:break
  except RuntimeError:pass
  time.sleep(.5)
 else:raise RuntimeError('Actual app role/streaming status failed')
 body={'request_id':secrets.token_hex(16),'author':'Automation runtime check','title':'Replicated issue','description':'From actual automation schema','status':'open','version':0}
 issue=api(apps[0],'/api/issues',body);assert api(apps[0],'/api/issues',body)['id']==issue['id']
 for _ in range(40):
  try:
   source=api(apps[0],'/api/issues/'+issue['id']);replica=api(apps[1],'/api/issues/'+issue['id'])
   if source==replica:break
  except RuntimeError:pass
  time.sleep(.25)
 else:raise RuntimeError('Issue/history replay failed')
 assert sql(primary,'SELECT count(*) FROM issues')=='1'
 assert sql(standby,'SELECT pg_is_in_recovery()')=='t'
 # Exercise the production promotion code against real PostgreSQL. Only K3s transport/systemctl are adapted.
 source=Path('deploy/terraform/failover/lambda/azure_command.py').read_text(encoding='utf-8')
 owner='arn:aws:states:us-east-1:123456789012:execution:dcs29-auto-failover:runtime'
 adapter="""
import psycopg, tempfile
from types import SimpleNamespace
ROOT=Path('/tmp/dcs29-runtime-receipt'); ROOT.mkdir(exist_ok=True)
(ROOT/'automation-result.json').write_text('{}')
subprocess.run=lambda *a,**k:SimpleNamespace(returncode=0)
def sql(query):
    with psycopg.connect(ADMIN_DSN,autocommit=True) as conn:
        cur=conn.execute(query)
        if not cur.description:return ''
        value=cur.fetchone()[0]
        return 't' if value is True else 'f' if value is False else str(value)
"""
 def command(phase,incident=owner):
  code=source+"\nADMIN_DSN="+repr('postgresql://postgres:'+passwords['POSTGRES_PASSWORD']+'@10.200.29.2:5432/issues')+'\n'+adapter+'\nmain('+repr(phase)+','+repr(incident)+')\n'
  return run(['exec','-i',apps[1],'python','-'],input=code.encode())
 assert 'standby' in command('preflight')
 run(['stop',primary])
 assert run(['inspect','--format','{{.State.Status}}',primary])=='exited'
 assert 'primary' in command('promote')
 assert 'primary' in command('promote') # Same incident retry must reuse the receipt.
 assert sql(standby,'SELECT pg_is_in_recovery()')=='f'
 state=api(apps[1],'/api/database/status');assert state['role']=='primary' and state['writable']
 assert api(apps[1],'/api/issues/'+issue['id'])==replica
 body['request_id']=secrets.token_hex(16);body['title']='Azure writer after automatic promotion'
 new=api(apps[1],'/api/issues',body);assert new['id']!=issue['id']
 assert api(apps[1],'/api/issues',body)['id']==new['id']
 try:command('promote',owner+'-other')
 except RuntimeError:pass
 else:raise RuntimeError('Another incident was incorrectly permitted')
 assert sql(standby,'SELECT count(*) FROM issues')=='2'
 print('PASS: real PG17 replication, fenced source, production promote+writer gate, incident retry/ownership, preserved issue/history, Azure writes and idempotent POST. K3s/systemctl transport adapted for Docker.')

finally:
 for n in containers:run(['rm','-f',n],check=False)
 for n in volumes:run(['volume','rm',n],check=False)
 run(['network','rm',network],check=False)
 env.unlink(missing_ok=True)
