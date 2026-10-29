"""Hash the staged Git snapshot, whose LF bytes are identical across supported OSes."""
import ast,hashlib,json,subprocess,tomllib
from pathlib import Path

ROOT=Path(__file__).resolve().parent
def main():
    names=subprocess.check_output(['git','-C',str(ROOT),'ls-files','-z']).decode().split('\0')
    payload={name:subprocess.check_output(['git','-C',str(ROOT),'show',':'+name]) for name in names if name and name!='release-manifest.json'}
    required={'install.py','update.py','setup_runtime.py','pyproject.toml','uv.lock','app/brain_settings.py','app/brain_service.py','app/brain_process.py','app/brain_windows_process.py','check_dependency_audit.py','security-exceptions.json','SECURITY.md'}
    if not required<=payload.keys():raise ValueError('Stage all required setup/controller/dependency files before building the release.')
    version=tomllib.loads(payload['pyproject.toml'].decode())['project']['version']
    def literal_version(name):
        values=[node.value.value for node in ast.parse(payload[name]).body if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='VERSION' for target in node.targets) and isinstance(node.value,ast.Constant)]
        if len(values)!=1:raise ValueError('One literal VERSION is required in '+name)
        return values[0]
    packages=[row['version'] for row in tomllib.loads(payload['uv.lock'].decode())['package'] if row['name']=='shared-second-brain']
    if packages!=[version] or any(literal_version(name)!=version for name in ('install.py','app/brain_settings.py')):
        raise ValueError('Staged project, lock, installer and app versions must agree.')
    hashes={name:hashlib.sha256(data).hexdigest() for name,data in payload.items()}
    (ROOT/'release-manifest.json').write_text(json.dumps({'version':version,'repository':'code-by-muhammad/second-brain-system',
        'capabilities':['sealed-client-refresh-v1','owned-service-control-v1'],'qualified_service_platforms':['win32'],'sha256':hashes},indent=2)+'\n',encoding='utf-8',newline='\n')
    print('Release',version,'contains',len(hashes),'hashed files.')
if __name__=='__main__': main()
