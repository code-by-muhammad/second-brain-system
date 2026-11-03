"""Read-only bounded disabled-state grammar, never labels or association values."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import runpy
import sys


def structure(raw):
    if len(raw)>2_000_000:raise RuntimeError('native-disabled-output-bound')
    rows=[]
    for line in raw.decode('utf-8').splitlines():
        value=line.strip()
        if not value:continue
        if re.fullmatch(r'[a-z ]+ = \{',value):rows.append({'header':value})
        elif value=='}':rows.append({'close':True})
        elif '=>' in value:
            key,_,tail=value.partition('=>');tail=tail.strip()
            rows.append({'row_key_shape':'quoted' if re.fullmatch(r'"[^"\r\n]+"',key.strip()) else 'other',
                'value_shape':tail if tail in ('disabled','enabled','true','false','{') else 'quoted' if re.fullmatch(r'"[^"\r\n]*"',tail) else 'other'})
        else:rows.append({'unknown_line':True})
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--receipt',required=True);args=parser.parse_args()
    report={'scope':'read-only disabled manager grammar, not product qualification','arch':platform.machine(),
        'os_version':platform.mac_ver()[0],'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        if sys.platform!='darwin' or os.geteuid()==0 or not sys.flags.isolated or os.environ.get('GITHUB_ACTIONS')!='true':
            raise RuntimeError('disposable-native-ci-required')
        source=Path(__file__).with_name('native_mac_manager_contract.py')
        code=runpy.run_path(str(source),run_name='contract_helpers')
        report['command_helper_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
        status,out,err=code['command']([code['protected']('/bin/launchctl'),'print-disabled','gui/'+str(os.geteuid())])
        report.update(exit_code=status,stderr_empty=not err,structure=structure(out))
        if status or err:raise RuntimeError('native-disabled-read-refused')
        report['status']='grammar observed; no mutation'
    except Exception as error:report.update(status='failed',failure_class=str(error) if type(error) is RuntimeError else type(error).__name__)
    path=Path(args.receipt);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2,sort_keys=True)
    return 0 if report.get('status')=='grammar observed; no mutation' else 1


if __name__=='__main__':raise SystemExit(main())
