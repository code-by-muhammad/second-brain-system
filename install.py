"""Install a tagged checkout. Device data stays outside this software repository."""
from __future__ import annotations
import argparse, hashlib, json, os, secrets, shutil, subprocess, sys
from pathlib import Path

REPO=Path(__file__).resolve().parent

def atomic(path,text):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp'); tmp.write_text(text,encoding='utf-8'); os.replace(tmp,path)

def backup(path,root):
    if path.exists():
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        target=root/'backups'/'client-configs'/digest
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists(): shutil.copy2(path,target)

def clients(root,python,home=None):
    home=home or Path.home()
    spec={'command':str(python),'args':[str(root/'app/brain_client.py')]}
    locations=[home/'.claude.json']
    if sys.platform=='win32': locations.append(Path(os.environ.get('APPDATA',home/'AppData/Roaming'))/'Claude/claude_desktop_config.json')
    elif sys.platform=='darwin': locations.append(home/'Library/Application Support/Claude/claude_desktop_config.json')
    changed=[]
    for path in locations:
        data=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        data.setdefault('mcpServers',{})['second-brain']=spec
        backup(path,root); atomic(path,json.dumps(data,indent=2,ensure_ascii=False)+'\n'); changed.append(str(path))
    path=home/'.codex/config.toml'
    content=path.read_text(encoding='utf-8') if path.exists() else ''
    import re, tomllib
    blocks=re.split(r'(?m)(?=^\[)',content)
    content=''.join(b for b in blocks if not re.match(r'^\[mcp_servers\.(?:second-brain|"second-brain")(?:\.|\])',b))
    content=content.rstrip()+'\n\n[mcp_servers.second-brain]\ncommand = '+json.dumps(str(python))+'\nargs = ['+json.dumps(str(root/'app/brain_client.py'))+']\nstartup_timeout_sec = 60\ntool_timeout_sec = 120\n'
    tomllib.loads(content)
    backup(path,root); atomic(path,content); changed.append(str(path))
    return changed

def deploy(root,python,vault,role='capture',configure_clients=False):
    if root==vault or root.is_relative_to(vault): raise ValueError('Runtime state must be outside the vault.')
    root.mkdir(parents=True,exist_ok=True)
    for folder in ('state','logs','models','backups'): (root/folder).mkdir(exist_ok=True)
    device=root/'device.json'
    old=json.loads(device.read_text()) if device.exists() else {}
    # Existing role and identity must not change implicitly during an upgrade.
    settings={**old,'vault':str(vault),'python':str(python),'release_root':str(REPO),
              'role':old.get('role',role),'device_id':old.get('device_id',secrets.token_hex(8)),
              'port':old.get('port',8765),'timezone':old.get('timezone','Asia/Karachi'),'version':'0.1.0'}
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
    if os.name!='nt':
        os.chmod(root/'state',0o700)
        for path in (access,root/'state/access.json',device): os.chmod(path,0o600)
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
    args=parser.parse_args(); root=Path(args.root).expanduser().resolve(); vault=Path(args.vault).expanduser().resolve()
    if not vault.is_dir(): parser.error('Clone/open the existing vault first; --vault must exist.')
    if args.reuse_python: python=Path(args.reuse_python).resolve()
    else:
        subprocess.run(['uv','sync','--locked','--no-dev','--project',str(REPO),'--python','3.12'],check=True)
        python=REPO/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
    result=deploy(root,python,vault,args.role,args.configure_clients)
    if args.download_model:
        code='from fastembed import TextEmbedding; TextEmbedding(model_name="BAAI/bge-small-en-v1.5",cache_dir='+repr(str(root/'models'))+',threads=4)'
        env=os.environ.copy(); env.pop('HF_HUB_OFFLINE',None); env['HF_HUB_DISABLE_TELEMETRY']='1'
        subprocess.run([str(python),'-c',code],env=env,check=True)
    if args.install_service:
        subprocess.run([str(python),str(root/'app/brain_cli.py'),'service','install'],check=True)
    print(json.dumps(result,indent=2))

if __name__=='__main__': main()
