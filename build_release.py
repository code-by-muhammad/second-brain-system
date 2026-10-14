"""Hash the staged Git snapshot, whose LF bytes are identical across supported OSes."""
import hashlib,json,subprocess,tomllib
from pathlib import Path

ROOT=Path(__file__).resolve().parent
def main():
    version=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    names=subprocess.check_output(['git','-C',str(ROOT),'ls-files','-z']).decode().split('\0')
    hashes={name:hashlib.sha256(subprocess.check_output(['git','-C',str(ROOT),'show',':'+name])).hexdigest()
            for name in names if name and name!='release-manifest.json'}
    (ROOT/'release-manifest.json').write_text(json.dumps({'version':version,'repository':'code-by-muhammad/second-brain-system','sha256':hashes},indent=2)+'\n',encoding='utf-8',newline='\n')
    print('Release',version,'contains',len(hashes),'hashed files.')
if __name__=='__main__': main()
