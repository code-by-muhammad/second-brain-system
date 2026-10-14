"""Install an explicit release from the owner's private repository; no tracking of main."""
import argparse, hashlib, json, os, re, subprocess, sys
from pathlib import Path

REMOTE='https://github.com/code-by-muhammad/second-brain-system.git'

def verify(checkout):
    manifest=json.loads((checkout/'release-manifest.json').read_text(encoding='utf-8'))
    required={'install.py','update.py','pyproject.toml','uv.lock'} | {p.relative_to(checkout).as_posix() for p in (checkout/'app').glob('*.py')}
    if not required <= set(manifest['sha256']): raise ValueError('Release manifest omits executable or dependency files.')
    for relative,expected in manifest['sha256'].items():
        target=(checkout/relative).resolve()
        if not target.is_relative_to(checkout.resolve()): raise ValueError('Unsafe manifest path.')
        if hashlib.sha256(target.read_bytes()).hexdigest()!=expected: raise ValueError('Release integrity mismatch: '+relative)
    return manifest

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True); parser.add_argument('--version',required=True)
    args=parser.parse_args(); root=Path(args.root).expanduser().resolve()
    if not re.fullmatch(r'v\d+\.\d+\.\d+',args.version): parser.error('Select an explicit version such as v0.1.0.')
    device=json.loads((root/'device.json').read_text())
    release=root/'releases'/args.version
    if not release.exists(): subprocess.run(['git','clone','--depth','1','--branch',args.version,'--',REMOTE,str(release)],check=True)
    info=verify(release)
    if info['version']!=args.version.removeprefix('v'): raise ValueError('Release version does not match requested tag.')
    subprocess.run(['uv','sync','--locked','--no-dev','--project',str(release),'--python','3.12'],check=True)
    python=release/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
    # Stop only this installation's registered service before switching its app files.
    subprocess.run([device['python'],str(root/'app/brain_cli.py'),'service','stop'],check=True)
    previous={'release_root':device['release_root'],'python':device['python'],'version':device['version']}
    (root/'state/previous-release.json').write_text(json.dumps(previous,indent=2),encoding='utf-8')
    try:
        subprocess.run([str(python),str(release/'install.py'),'--root',str(root),'--vault',device['vault'],'--reuse-python',str(python),'--configure-clients','--install-service'],check=True)
        subprocess.run([str(python),str(root/'app/brain_cli.py'),'service','start'],check=True)
        import time
        time.sleep(10)
        subprocess.run([str(python),str(root/'app/brain_cli.py'),'health'],check=True)
    except Exception:
        old=Path(previous['release_root'])/'install.py'
        subprocess.run([previous['python'],str(old),'--root',str(root),'--vault',device['vault'],'--reuse-python',previous['python'],'--configure-clients','--install-service'],check=True)
        subprocess.run([previous['python'],str(root/'app/brain_cli.py'),'service','start'],check=True)
        raise
    print(json.dumps({'status':'updated','version':info['version'],'previous':previous}))

if __name__=='__main__': main()
