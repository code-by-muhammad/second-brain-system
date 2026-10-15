"""Install a tagged checkout. Device data stays outside this software repository."""
from __future__ import annotations
import argparse, hashlib, json, os, secrets, shutil, stat, subprocess, sys, tempfile
from pathlib import Path

REPO=Path(__file__).resolve().parent
VERSION='0.1.7'

def private(path,directory=False):
    """Restrict a local state object before putting private content in it."""
    if os.name=='nt':
        account=os.environ.get('USERDOMAIN','.')+'\\'+os.environ.get('USERNAME','')
        flags='(OI)(CI)F' if directory else 'F'
        subprocess.run(['icacls.exe',str(path),'/inheritance:r','/grant:r',account+':'+flags,'SYSTEM:'+flags],
                       check=True,stdout=subprocess.DEVNULL)
    else: os.chmod(path,0o700 if directory else 0o600)

def private_dir(path):
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    private(path,True)

def atomic(path,text):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    mode=stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=path.parent)
    tmp=Path(name)
    try:
        # mkstemp is owner-only on POSIX. On Windows restrict its empty ACL before
        # writing, since the CRT mode bits alone do not enforce DACL privacy.
        if os.name=='nt': private(tmp)
        options={} if isinstance(text,bytes) else {'encoding':'utf-8','newline':''}
        with os.fdopen(fd,'wb' if isinstance(text,bytes) else 'w',**options) as output:
            fd=None
            output.write(text); output.flush(); os.fsync(output.fileno())
        if os.name!='nt': os.chmod(tmp,mode)
        os.replace(tmp,path)
    finally:
        if fd is not None: os.close(fd)
        tmp.unlink(missing_ok=True)

def backup(path,root):
    if path.exists():
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        target=root/'backups'/'client-configs'/digest
        private_dir(target.parent)
        if not target.exists(): atomic(target,path.read_bytes())

def client_plan(root,python,home=None):
    home=home or Path.home()
    spec={'command':str(python),'args':[str(root/'app/brain_client.py')]}
    locations=[home/'.claude.json']
    if sys.platform=='win32': locations.append(Path(os.environ.get('APPDATA',home/'AppData/Roaming'))/'Claude/claude_desktop_config.json')
    elif sys.platform=='darwin': locations.append(home/'Library/Application Support/Claude/claude_desktop_config.json')
    plan=[]
    for path in locations:
        data=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if not isinstance(data,dict) or not isinstance(data.get('mcpServers',{}),dict):
            raise ValueError(f'Invalid MCP client object: {path}')
        data.setdefault('mcpServers',{})['second-brain']=spec
        plan.append((path,json.dumps(data,indent=2,ensure_ascii=False)+'\n'))
    path=home/'.codex/config.toml'
    content=path.read_text(encoding='utf-8') if path.exists() else ''
    import re, tomllib
    tomllib.loads(content)  # Validate the original before stripping any managed block.
    blocks=re.split(r'(?m)(?=^\[)',content)
    content=''.join(b for b in blocks if not re.match(r'^\[mcp_servers\.(?:second-brain|"second-brain")(?:\.|\])',b))
    content=content.rstrip()+'\n\n[mcp_servers.second-brain]\ncommand = '+json.dumps(str(python))+'\nargs = ['+json.dumps(str(root/'app/brain_client.py'))+']\nstartup_timeout_sec = 60\ntool_timeout_sec = 120\n'
    tomllib.loads(content)
    plan.append((path,content))
    # Install the shared protocol as a managed block, preserving the owner's other instructions.
    instructions=home/'.codex/AGENTS.md'
    previous=instructions.read_text(encoding='utf-8') if instructions.exists() else ''
    begin='<!-- BEGIN SECOND-BRAIN -->'; end='<!-- END SECOND-BRAIN -->'
    if previous.count(begin)!=previous.count(end): raise ValueError(f'Incomplete managed protocol block: {instructions}')
    previous=re.sub(re.escape(begin)+r'.*?'+re.escape(end),'',previous,flags=re.S).rstrip()
    protocol=(REPO/'docs/AI-PROTOCOL.md').read_text(encoding='utf-8')
    plan.append((instructions,previous+'\n\n'+begin+'\n'+protocol.rstrip()+'\n'+end+'\n'))
    settings_path=home/'.claude/settings.json'
    settings=json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.exists() else {}
    if not isinstance(settings,dict) or not isinstance(settings.get('hooks',{}),dict):
        raise ValueError(f'Invalid Claude hooks object: {settings_path}')
    import shlex
    command=('"'+str(python).replace('\\','/')+'" "'+str(root/'app/brain_hooks.py').replace('\\','/')+'"') if os.name=='nt' else shlex.join([str(python),str(root/'app/brain_hooks.py')])
    for event in ('SessionStart','Stop'):
        entries=[]
        prior=settings.setdefault('hooks',{}).get(event,[])
        if not isinstance(prior,list): raise ValueError(f'Invalid Claude {event} hooks: {settings_path}')
        for entry in prior:
            if not isinstance(entry,dict) or not isinstance(entry.get('hooks',[]),list) or any(
                    not isinstance(handler,dict) or not isinstance(handler.get('command',''),str) for handler in entry.get('hooks',[])):
                raise ValueError(f'Invalid Claude {event} hook entry: {settings_path}')
            handlers=[handler for handler in entry.get('hooks',[]) if 'brain_hooks.py' not in handler.get('command','')]
            if handlers: entries.append({**entry,'hooks':handlers})
        entries.append({'hooks':[{'type':'command','command':command,'timeout':10}]})
        settings['hooks'][event]=entries
    plan.append((settings_path,json.dumps(settings,indent=2,ensure_ascii=False)+'\n'))
    return plan

def clients(root,python,home=None):
    plan=client_plan(root,python,home)
    for path,text in plan:
        backup(path,root); atomic(path,text)
    return [str(path) for path,_ in plan]

def validate(root,vault,python,configure_clients=False):
    if root==vault or root.is_relative_to(vault) or vault.is_relative_to(root):
        raise ValueError('Runtime and vault must be separate, non-nested directories.')
    device=root/'device.json'
    old=json.loads(device.read_text(encoding='utf-8')) if device.exists() else {}
    if not isinstance(old,dict): raise ValueError('Invalid existing device configuration.')
    if old.get('vault') and Path(old['vault']).expanduser().resolve()!=vault.resolve():
        raise ValueError('Changing --vault on an existing runtime is unsupported; create a new runtime and rebuild its local index.')
    config=root/'state/config.json'
    if config.exists():
        indexed=json.loads(config.read_text(encoding='utf-8'))
        if not isinstance(indexed,dict): raise ValueError('Invalid existing retrieval configuration.')
        projects=indexed.get('projects',{})
        if not isinstance(projects,dict): raise ValueError('Invalid existing project configuration.')
        project=projects.get('brain',{})
        if not isinstance(project,dict): raise ValueError('Invalid existing brain project configuration.')
        for existing in (indexed.get('project_root'),project.get('path')):
            if existing and Path(existing).expanduser().resolve()!=vault.resolve():
                raise ValueError('Existing index targets another vault; create a new runtime and rebuild its local index.')
    if configure_clients: client_plan(root,python)
    return old

def deploy(root,python,vault,role='capture',configure_clients=False):
    old=validate(root,vault,python,configure_clients)
    for folder in ('state','logs','models','backups'): private_dir(root/folder)
    device=root/'device.json'
    # Existing role and identity must not change implicitly during an upgrade.
    settings={**old,'vault':str(vault),'python':str(python),'release_root':str(REPO),
              'role':old.get('role',role),'device_id':old.get('device_id',secrets.token_hex(8)),
              'port':old.get('port',8765),'timezone':old.get('timezone','Asia/Karachi'),'version':VERSION}
    backup(device,root)
    atomic(device,json.dumps(settings,indent=2)+'\n')
    installed=root/'app'
    if installed.exists():
        old_digest=hashlib.sha256(b''.join(p.read_bytes() for p in sorted(installed.glob('*.py')))).hexdigest()[:16]
        target=root/'backups'/'app'/old_digest
        if not target.exists(): shutil.copytree(installed,target)
    installed.mkdir(exist_ok=True)
    for source in (REPO/'app').glob('*.py'): shutil.copy2(source,installed/source.name)
    access=root/'state/client-access.json'
    if not access.exists():
        token=secrets.token_urlsafe(48)
        atomic(access,json.dumps({'token':token})+'\n')
        atomic(root/'state/access.json',json.dumps({'token_sha256':hashlib.sha256(token.encode()).hexdigest()})+'\n')
    for path in (access,root/'state/access.json',device): private(path)
    config=root/'state/config.json'
    if not config.exists():
        atomic(config,json.dumps({'projects':{'brain':{'path':str(vault),'mode':'local'}},'default_project':'brain','project_root':str(vault),
               'database_backend':'sqlite','semantic_search_enabled':True,'semantic_embedding_provider':'fastembed',
               'semantic_embedding_model':'bge-small-en-v1.5','semantic_embedding_dimensions':384,
               'semantic_embedding_cache_dir':str(root/'models'),'semantic_embedding_threads':4,'semantic_embedding_batch_size':8,
               'auto_update':False,'logfire_enabled':False,'logfire_send_to_logfire':False,'disable_permalinks':True,
               'ensure_frontmatter_on_sync':False,'format_on_save':False},indent=2)+'\n')
    atomic(root/'state/.bmignore','.git/\n.obsidian/\n.trash/\n')
    changed=clients(root,python) if configure_clients else []
    return {'status':'installed','root':str(root),'vault':str(vault),'role':settings['role'],'device_id':settings['device_id'],'clients':changed}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault',required=True); parser.add_argument('--root',default=str(Path.home()/'.local/share/second-brain'))
    parser.add_argument('--role',choices=['capture','maintainer'],default='capture')
    parser.add_argument('--configure-clients',action='store_true'); parser.add_argument('--install-service',action='store_true')
    parser.add_argument('--download-model',action='store_true'); parser.add_argument('--reuse-python')
    parser.add_argument('--validate-only',action='store_true',help='Check configuration without changing runtime, clients or services.')
    args=parser.parse_args(); root=Path(args.root).expanduser().resolve(); vault=Path(args.vault).expanduser().resolve()
    if not vault.is_dir(): parser.error('Clone/open the existing vault first; --vault must exist.')
    if args.reuse_python: python=Path(os.path.abspath(os.path.expanduser(args.reuse_python)))
    else:
        if args.validate_only: parser.error('--validate-only requires --reuse-python.')
        subprocess.run(['uv','sync','--locked','--no-dev','--project',str(REPO),'--python','3.12'],check=True)
        python=REPO/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
    if not python.is_file(): parser.error('The selected Python executable does not exist.')
    if args.validate_only:
        validate(root,vault,python,args.configure_clients)
        print(json.dumps({'status':'validated'})); return
    result=deploy(root,python,vault,args.role,args.configure_clients)
    if args.download_model:
        code='from fastembed import TextEmbedding; TextEmbedding(model_name="BAAI/bge-small-en-v1.5",cache_dir='+repr(str(root/'models'))+',threads=4)'
        env=os.environ.copy(); env.pop('HF_HUB_OFFLINE',None); env['HF_HUB_DISABLE_TELEMETRY']='1'
        subprocess.run([str(python),'-c',code],env=env,check=True)
    if args.install_service:
        subprocess.run([str(python),str(root/'app/brain_cli.py'),'service','install'],check=True)
    print(json.dumps(result,indent=2))

if __name__=='__main__': main()
