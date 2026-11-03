"""Synthetic store/daily/Git case identity; baseline observation is not acceptance.

CLI bootstraps the existing sandbox conftest before any product import. No notes,
service, model, account or network remote outside that fresh sandbox is used.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
import platform
from pathlib import Path
import sys
from types import SimpleNamespace
import unicodedata
import uuid

import pytest

if __name__!='__main__':
    import brain_store as s
    import brain_maintenance as m
    import brain_git as g
    from brain_markdown import frontmatter
    from test_sync_fsmonitor_policy import isolated_git,_init,_raw


TARGET='02-Areas/AI-Memory/Overview.md'
ALIAS='02-Areas/AI-Memory/overview.md'
SOURCE='01-Projects/Source.md'


def names(directory):
    with os.scandir(directory) as entries:return sorted(entry.name for entry in entries)


def source_snapshot(source):
    paths=sorted((source/'app').glob('*.py'))
    paths+=[source/name for name in ('pyproject.toml','tests/conftest.py','tests/isolation_guard.py','tests/test_sync_fsmonitor_policy.py')]
    return {p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def same_existing(left,right):
    return left.exists() and right.exists() and left.samefile(right)


def prepare(brain):
    vault,state=brain
    source=vault/SOURCE;source.parent.mkdir(parents=True);source.write_bytes(b'# Synthetic source\nDo not alter this supporting evidence.\n')
    target=vault/TARGET;target.parent.mkdir(parents=True);target.write_bytes(b'# Synthetic original derived page\nPreserve its spelling.\n')
    template=vault/'Templates/daily-note.md';template.parent.mkdir();template.write_bytes(b'# Synthetic template\n\n## Work Log\n')
    receipt=s.capture('case-identity-synthetic','Synthetic milestone','Synthetic verified result.','tests','disposable local fixture')
    capture=vault/receipt['path'];raw=capture.read_text(encoding='utf-8');meta=frontmatter(raw)
    when=datetime.fromisoformat(meta['captured_at'])
    requested=vault/('05-Daily/'+when.strftime('%Y-%m-%d-%A')+'.md')
    daily=requested.with_suffix('.MD');daily.parent.mkdir();daily.write_bytes(b'# Existing synthetic owner daily\n\n## Work Log\n- Owner entry remains.\n')
    return dict(vault=vault,state=state,source=source,target=target,template=template,
        capture=capture,capture_raw=raw,meta=meta,when=when,daily=daily,daily_request=requested)


def audit(state,action):
    path=state/'audit.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if json.loads(line).get('action')==action]


def update(alias,sha):
    return s.update_current(alias,'Synthetic current summary','Supported synthetic content.',[SOURCE],sha)


def call_update(alias,sha):
    try:return {'result':update(alias,sha)}
    except ValueError as error:return {'error_type':type(error).__name__,'error':str(error)}


def write_receipt(value):
    supplied=os.environ.get('SB_CASE_IDENTITY_RECEIPT')
    if supplied:
        path=Path(supplied)
        if not path.is_absolute() or path.exists():raise RuntimeError('Require a fresh absolute receipt.')
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)


def test_native_case_identity_observation(brain,tmp_path,isolated_git):
    phase=os.environ.get('SB_CASE_IDENTITY_PHASE','fixed')
    assert phase in ('baseline','fixed')
    value={'phase':phase,'status':'incomplete observation; not fixed acceptance','stage':'initialization'}
    try:
        value['source_sha256']=source_snapshot(Path(s.__file__).resolve().parents[1])
        value['harness_sha256']=s.digest(Path(__file__).read_bytes())
        value['stage']='prepare'
        f=prepare(brain);vault,state=f['vault'],f['state']
        before={k:f[k].read_bytes() for k in ('source','target','template','capture','daily')}
        case_alias=same_existing(vault/ALIAS,f['target'])
        daily_alias=same_existing(f['daily_request'],f['daily'])
        value['stage']='local Git setup'
        remote=tmp_path/'synthetic-case-remote.git';_init(remote,isolated_git,bare=True);_init(vault,isolated_git)
        _raw(vault,'add','.');_raw(vault,'commit','-m','synthetic baseline');_raw(vault,'remote','add','origin',str(remote));_raw(vault,'push','-u','origin','HEAD')
        tracked=_raw(vault,'ls-files','-z').decode('utf-8').split('\0');tracked=[x for x in tracked if x]
        remote_before=_raw(remote,'rev-parse','refs/heads/main').decode().strip()
        value['stage']='update_current'
        changed=call_update(ALIAS,s.digest(before['target']))
        value['stage']='daily_link'
        daily_linked=m._daily_link(f['capture'],f['capture_raw'],f['meta'],f['when'])
        update_audit=audit(state,'update_current');daily_audit=audit(state,'daily_capture_link')
        daily_relative=f['daily'].relative_to(vault).as_posix()
        disk_case={rel:g._worktree_case_path(vault,rel,{}) for rel in (TARGET,daily_relative)}
        value['stage']='git_sync'
        git_result=(g.sync(vault) if case_alias and daily_alias else 'not run: distinct case-sensitive paths; no alias or portable-collision acceptance inferred')
        remote_after=_raw(remote,'rev-parse','refs/heads/main').decode().strip()
        # Unicode observation is separate from Git's platform-specific normalization.
        value['stage']='unicode'
        nfd=unicodedata.normalize('NFD','Caf\u00e9.md');nfc=unicodedata.normalize('NFC','Caf\u00e9.md')
        unicode_path=f['target'].parent/nfd;unicode_path.write_bytes(b'# Synthetic normalization spelling\n')
        unicode_before=unicode_path.read_bytes();unicode_request=unicode_path.with_name(nfc)
        normalization_alias=same_existing(unicode_request,unicode_path)
        unicode_names_before=names(unicode_path.parent)
        unicode_actual=[name for name in unicode_names_before if (unicode_path.parent/name).samefile(unicode_path)]
        assert len(unicode_actual)==1
        unicode_actual_path=unicode_path.with_name(unicode_actual[0])
        unicode_result=call_update(unicode_request.relative_to(vault).as_posix(),s.digest(unicode_before))
        value.update({'phase':phase,'status':'observation only; not fixed acceptance' if phase=='baseline' else 'fixed assertions pending',
            'source_sha256':source_snapshot(Path(s.__file__).resolve().parents[1]),
            'tools':{'python':sys.version,'pytest':pytest.__version__,'git':_raw(vault,'--version').decode('utf-8').strip(),
                     'machine':platform.machine(),'system':platform.system(),'release':platform.release()},
            'harness_sha256':s.digest(Path(__file__).read_bytes()),'platform':sys.platform,
            'case_alias_samefile':case_alias,'daily_extension_alias_samefile':daily_alias,
            'update':changed,'target_names_after':names(f['target'].parent),'update_audit_paths':[v['path'] for v in update_audit],
            'daily_linked':daily_linked,'daily_names_after':names(f['daily'].parent),'daily_audit_paths':[v['path'] for v in daily_audit],
            'source_template_capture_unchanged':all(f[k].read_bytes()==before[k] for k in ('source','template','capture')),
            'git':{'tracked_before':tracked,'case_aliases_after':disk_case,'sync_result':git_result,
                   'remote_before':remote_before,'remote_after':remote_after,
                   'remote_url':_raw(vault,'remote','get-url','origin').decode('utf-8').strip(),
                   'file_only_disposable_remote':Path(_raw(vault,'remote','get-url','origin').decode('utf-8').strip()).resolve()==remote.resolve()},
            'unicode':{'created_nfd_name':nfd,'requested_nfc_name':nfc,'actual_name_before':unicode_actual[0],'names_before':unicode_names_before,'alias_samefile':normalization_alias,
                'result':unicode_result,'names_after':names(f['target'].parent),'git_normalization_qualified':False},
            'scope':'Synthetic actual update_current/_daily_link, native directory identity and local file-only Git; no original notes, model, service, external remote or universal OS-case claim.'})
        value['stage']='assertions'
        assert value['source_template_capture_unchanged']
        assert value['git']['file_only_disposable_remote']
        if git_result.startswith('paused'):assert remote_after==remote_before
        if not case_alias:
            assert 'error_type' in changed and 'Concurrent change' in changed['error']
            assert f['target'].read_bytes()==before['target'] and not (vault/ALIAS).exists()
        else:assert changed['result']['status']=='saved'
        if phase=='fixed':
            if case_alias:
                assert changed['result']['path']==TARGET and update_audit[-1]['path']==TARGET
                assert f['target'].name in names(f['target'].parent)
                assert disk_case[TARGET] is None
            if daily_alias:
                assert daily_audit[-1]['path']==daily_relative and f['daily'].name in names(f['daily'].parent)
                assert disk_case[daily_relative] is None
            else:assert f['daily'].read_bytes()==before['daily']
            if case_alias and daily_alias:assert git_result=='synced' and remote_after!=remote_before
            if normalization_alias:
                assert 'result' in unicode_result,unicode_result
                assert unicode_result['result']['path']==unicode_actual_path.relative_to(vault).as_posix()
                assert unicode_actual[0] in names(unicode_path.parent)
            else:
                assert 'error_type' in unicode_result and unicode_path.read_bytes()==unicode_before
            assert m._daily_link(f['capture'],f['capture_raw'],f['meta'],f['when']) is False
            value['status']='fixed assertions passed; scoped synthetic evidence only'
    except BaseException as error:
        value.update(status='failed observation; not fixed acceptance',error_type=type(error).__name__,error=str(error))
        raise
    finally:write_receipt(value)


def test_stale_sha_refuses_without_recasing_or_audit(brain):
    f=prepare(brain);before=names(f['target'].parent);old=f['target'].read_bytes();entries=len(audit(f['state'],'update_current'))
    with pytest.raises(ValueError,match='Concurrent change'):update(ALIAS,'0'*64)
    assert names(f['target'].parent)==before and f['target'].read_bytes()==old
    assert len(audit(f['state'],'update_current'))==entries and not (f['state']/'write-backups').exists()


def test_mistyped_allowed_prefix_is_not_new_write_authority(brain):
    f=prepare(brain);old=f['target'].read_bytes();before=names(f['target'].parent)
    with pytest.raises(ValueError,match='designated'):update('02-areas/ai-memory/Overview.md',s.digest(old))
    assert f['target'].read_bytes()==old and names(f['target'].parent)==before


@pytest.mark.parametrize('sibling',['regular','symlink'])
def test_exact_entry_is_preferred_before_earlier_casefold_siblings(brain,monkeypatch,sibling):
    f=prepare(brain);target=f['target'];scan=s.os.scandir;link=Path.is_symlink;same=Path.samefile;inspected=[]
    alias='overview.MD'
    @contextmanager
    def ordered(path):
        if Path(path)==target.parent:yield iter([SimpleNamespace(name=alias),SimpleNamespace(name=target.name)])
        else:
            with scan(path) as rows:yield rows
    def is_link(path):
        if path.parent==target.parent and path.name==alias:
            inspected.append('alias-link');return sibling=='symlink'
        return link(path)
    def samefile(path,other):
        if Path(other).parent==target.parent and Path(other).name==alias:
            inspected.append('alias-stat');return False
        return same(path,other)
    monkeypatch.setattr(s.os,'scandir',ordered);monkeypatch.setattr(Path,'is_symlink',is_link);monkeypatch.setattr(Path,'samefile',samefile)
    assert s._existing_write_path(target).name==target.name
    assert inspected==[]


def test_owner_case_rename_during_staging_is_not_undone(brain,monkeypatch):
    f=prepare(brain);target=f['target'];old=target.read_bytes();create=s.tempfile.mkstemp;flush=s.os.fsync;staged=set();renamed=[]
    def staging(*args,**kwargs):
        fd,path=create(*args,**kwargs)
        if kwargs.get('prefix')=='.brain-':staged.add(fd)
        return fd,path
    def fsync(fd):
        flush(fd)
        if fd in staged and not renamed:
            temp=target.with_name('owner-case-move.tmp');actual=target.with_name('overview.md')
            os.rename(target,temp);os.rename(temp,actual);renamed.append(actual)
    monkeypatch.setattr(s.tempfile,'mkstemp',staging);monkeypatch.setattr(s.os,'fsync',fsync)
    with pytest.raises(ValueError,match='(?i)concurrent.*change|spelling'):update(TARGET,s.digest(old))
    assert renamed and renamed[0].read_bytes()==old and names(target.parent)==['overview.md']
    assert not audit(f['state'],'update_current') and (f['state']/'write-backups'/s.digest(old)).read_bytes()==old


def main():
    sys.dont_write_bytecode=True
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('baseline','fixed'),required=True)
    parser.add_argument('--source-repository',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    if not args.source_repository.is_absolute() or not args.receipt.is_absolute():raise RuntimeError('Absolute source and receipt paths required.')
    source=args.source_repository.absolute();receipt=args.receipt.absolute()
    if not source.is_absolute() or not (source/'tests/conftest.py').is_file() or not (source/'app/brain_store.py').is_file():raise RuntimeError('Select the actual trusted source checkout.')
    if receipt.exists() or receipt.suffix!='.json' or receipt.with_suffix('.xml').exists() or receipt.with_suffix('.summary.json').exists():raise RuntimeError('Fresh JSON/XML/summary receipt paths required.')
    receipt.parent.mkdir(parents=True,exist_ok=True)
    sandbox=Path('C:/SB-Test' if os.name=='nt' else '/private/tmp')/('store-case-'+uuid.uuid4().hex)
    if sandbox.exists():raise RuntimeError('Fresh synthetic sandbox required.')
    sys.pycache_prefix=str(sandbox/'import-cache')
    os.environ['SB_TEST_SANDBOX']=str(sandbox);os.environ['SB_CASE_IDENTITY_PHASE']=args.phase;os.environ['SB_CASE_IDENTITY_RECEIPT']=str(receipt)
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    sys.path[:0]=[str(source/'tests'),str(source/'app')]
    before=source_snapshot(source)
    invocation=['--noconftest','--import-mode=importlib','-p','conftest','-p','no:cacheprovider','-c',str(source/'pyproject.toml'),'--rootdir',str(source),'--basetemp',str(sandbox/'cases'),'-q','--junitxml',str(receipt.with_suffix('.xml')),str(Path(__file__).resolve())]
    if args.phase=='baseline':invocation+=['-k','native_case_identity_observation']
    code=pytest.main(invocation)
    after=source_snapshot(source)
    observed=json.loads(receipt.read_text(encoding='utf-8')) if receipt.exists() else None
    recorded=isinstance(observed,dict) and observed.get('phase')==args.phase and observed.get('source_sha256')==before
    summary={'phase':args.phase,'pytest_exit':int(code),'source_sha256_before':before,'source_sha256_after':after,'source_unchanged':before==after,
        'baseline_is_acceptance':False,'observation_recorded':recorded,
        'baseline_observation_complete':args.phase=='baseline' and int(code)==0 and before==after and recorded and observed.get('status')=='observation only; not fixed acceptance',
        'fixed_suite_passed':args.phase=='fixed' and int(code)==0 and before==after and recorded
            and observed.get('status')=='fixed assertions passed; scoped synthetic evidence only','receipt_exists':receipt.exists(),
        'explicit_conftest':str(source/'tests/conftest.py'),'automatic_conftest_discovery':False,'entrypoint_plugin_autoload':False,'sandbox':str(sandbox),'scope':'Fresh synthetic actual APIs and file-only Git; original/shared vault never used.'}
    with receipt.with_suffix('.summary.json').open('x',encoding='utf-8') as stream:json.dump(summary,stream,indent=2)
    print(json.dumps({key:summary[key] for key in ('phase','pytest_exit','source_unchanged','observation_recorded','baseline_observation_complete','fixed_suite_passed','receipt_exists')}))
    return int(code) if before==after and (summary['baseline_observation_complete'] or summary['fixed_suite_passed']) else 1


if __name__=='__main__':raise SystemExit(main())
