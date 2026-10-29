"""Fail closed on audit findings outside the exact time-limited local profile."""
import argparse
from datetime import date,datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parent


def assess(report,policy,root=ROOT,*,today=None):
    if not isinstance(report,dict) or not isinstance(report.get('dependencies'),list):
        raise ValueError('Dependency audit did not return a complete dependency list.')
    if not isinstance(policy,dict) or policy.get('format')!=1 or policy.get('platform')!='win32' or not isinstance(policy.get('exceptions'),list):
        raise ValueError('Unsupported audit exception policy.')
    for item in policy['exceptions']:
        if (not isinstance(item,dict) or not all(isinstance(item.get(key),str) and item[key] for key in ('package','version','id','review_by'))
                or item.get('disposition')!='not_reachable_in_qualified_profile'):
            raise ValueError('Malformed advisory exception requires review.')
        try:date.fromisoformat(item['review_by'])
        except ValueError:raise ValueError('Malformed exception review deadline.') from None
    dependencies=report['dependencies']
    if not dependencies:raise ValueError('An empty dependency audit is not accepted.')
    findings=[];versions={}
    for package in dependencies:
        if (not isinstance(package,dict) or package.get('skip_reason') or not isinstance(package.get('name'),str)
                or not re.fullmatch(r'[A-Za-z0-9_.-]+',package['name']) or not isinstance(package.get('version'),str)
                or not isinstance(package.get('vulns'),list)):
            raise ValueError('Unscanned or malformed dependency metadata requires review.')
        name=re.sub(r'[-_.]+','-',package['name']).lower()
        if name in versions:raise ValueError('Duplicate audit dependency requires review.')
        versions[name]=package['version']
        for vulnerability in package['vulns']:
            identifier=vulnerability.get('id') if isinstance(vulnerability,dict) else None
            if not isinstance(identifier,str) or not re.fullmatch(r'(?:GHSA|PYSEC|CVE)-[A-Za-z0-9-]+',identifier):
                raise ValueError('Malformed advisory identifier requires review.')
            findings.append((name,package['version'],identifier))
    if 'litellm' in versions:raise ValueError('The excluded LiteLLM package appeared in the audit.')
    if not isinstance(policy.get('expected_audit_versions'),dict) or versions!=policy['expected_audit_versions']:
        raise ValueError('The audit does not cover the exact qualified dependency graph.')
    applied=[];unresolved=[]
    for finding in findings:
        matches=[item for item in policy['exceptions'] if (item.get('package'),item.get('version'),item.get('id'))==finding]
        if len(matches)!=1:unresolved.append(finding);continue
        item=matches[0]
        if (today or datetime.now(timezone.utc).date())>date.fromisoformat(item['review_by']):
            raise ValueError('An advisory exception expired; review before release.')
        if item.get('disposition')!='not_reachable_in_qualified_profile':raise ValueError('Unsupported exception disposition.')
        applied.append(finding)
    if applied:
        if versions.get('basic-memory')!='0.23.2':raise ValueError('The reviewed Basic Memory dependency changed.')
        hashes=policy.get('source_sha256')
        if not isinstance(hashes,dict) or not hashes:raise ValueError('Missing exception source binding.')
        for name,expected in hashes.items():
            path=(Path(root)/name)
            if not path.resolve().is_relative_to(Path(root).resolve()) or path.is_symlink() or path.is_junction():
                raise ValueError('Unsafe exception source binding.')
            if not re.fullmatch('[a-f0-9]{64}',expected) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                raise ValueError('Protected security profile changed; independent review is required.')
    return {'status':'needs review' if unresolved else ('passed with scoped exception' if applied else 'passed; no findings'),
            'raw_findings':len(findings),'exceptions_applied':[{'package':p,'version':v,'id':i} for p,v,i in applied],
            'unresolved':[{'package':p,'version':v,'id':i} for p,v,i in unresolved],
            'audit_claim':'raw audit findings and exceptions remain separate; no blanket clean-audit claim'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('report',type=Path);args=parser.parse_args()
    try:
        if sys.platform!='win32':raise ValueError('This audit profile is qualified for Windows only.')
        if args.report.stat().st_size>16_000_000:raise ValueError('Audit report exceeds its bound.')
        report=json.loads(args.report.read_bytes());policy=json.loads((ROOT/'security-exceptions.json').read_bytes())
        result=assess(report,policy)
    except (OSError,ValueError,KeyError,TypeError):
        parser.exit(2,'Dependency audit/profile validation failed; retain the raw report for review.\n')
    print(json.dumps(result,indent=2))
    if result['unresolved']:raise SystemExit(1)


if __name__=='__main__':main()
