"""Actual Git objects must support historical quotes; receipts alone cannot."""
import copy
import os
from pathlib import Path
import subprocess

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture


def git_fixture(tmp_path, vault):
    config=tmp_path/'history.gitconfig'; config.write_bytes(b'')
    hooks=tmp_path/'history-hooks'; hooks.mkdir()
    env=os.environ.copy(); env.update(GIT_CONFIG_GLOBAL=str(config),GIT_CONFIG_NOSYSTEM='1')
    def git(*args, cwd=vault):
        return subprocess.run(['git','-c','user.name=History Test','-c','user.email=history@example.invalid',
                               '-c','commit.gpgsign=false','-c','core.autocrlf=false','-c','core.hooksPath='+str(hooks),*args],
                              cwd=cwd,env=env,capture_output=True,check=True)
    git('init','--initial-branch=main')
    return git


def replace_meta(path, info):
    path.write_bytes(s.note_text(info,'# Preserved historical topic\n').encode())


def legacy_history(brain,tmp_path,newline='\n',bom=False,uncertain=False):
    vault,state=brain; git=git_fixture(tmp_path,vault)
    source,initial,result=fixture(brain)
    text=w.normalized(source.read_text(encoding='utf-8'))
    raw=(('\ufeff' if bom else '')+text.replace('\n',newline)).encode()
    source.write_bytes(raw); packet=w.prepare(); w.apply(packet,result)
    topic=vault/w.BASE/'Projects/example.md'
    info=w.meta(topic.read_text(encoding='utf-8'))
    for claim in info['wiki_claims']: claim.pop('source_revision',None)
    replace_meta(topic,info)
    git('add','--','.'); git('commit','-m','Original exact source and legacy wiki')
    original_commit=git('rev-parse','HEAD').stdout.decode().strip()
    original_blob=git('rev-parse','HEAD:'+source.relative_to(vault).as_posix()).stdout.decode().strip()
    quote=result['pages'][0]['claims'][0]['quote']
    source.write_bytes(text.replace(quote,'A later owner revision describes a different review outcome.').replace('2026-10-01','2026-10-02').encode())
    info['source_hashes'][source.relative_to(vault).as_posix()]=s.digest(source.read_bytes())
    if uncertain: info['wiki_claims'][0]['status']='uncertain'
    replace_meta(topic,info)
    git('add','--','.'); git('commit','-m','Owner revision and legacy overwritten source map')
    return source,topic,result,s.digest(raw),original_commit,original_blob,git


@pytest.mark.parametrize('newline,bom',[('\n',False),('\r\n',False),('\r',False),('\n',True),('\r\n',True)])
def test_legacy_overwritten_map_recovers_actual_source_blob_without_mutation(brain,tmp_path,newline,bom):
    source,topic,_,old_sha,commit,blob,_=legacy_history(brain,tmp_path,newline,bom)
    source_raw=source.read_bytes(); topic_raw=topic.read_bytes()
    report=w.lint()
    assert not report['issues']
    assert report['historical_evidence_count']==1
    proof=report['verified_historical_evidence'][0]
    assert proof['source_sha256']==old_sha and proof['git_commit']==commit and proof['git_blob']==blob
    assert source.read_bytes()==source_raw and topic.read_bytes()==topic_raw
    assert report['history_verification']['git_commands']<=256
    assert report['history_verification']['git_blob_bytes']<=16_000_000
    assert 'quote' not in proof  # Diagnostics contain identifiers, not source excerpts.


def test_legacy_status_transition_preserves_uncertainty_and_finds_same_evidence(brain,tmp_path):
    _,topic,_,old_sha,_,_,_=legacy_history(brain,tmp_path,uncertain=True)
    report=w.lint()
    assert not report['issues'] and report['verified_historical_evidence'][0]['source_sha256']==old_sha
    assert w.meta(topic.read_text())['wiki_claims'][0]['status']=='uncertain'


def test_new_claim_anchors_exact_supporting_revision_and_receipt_fingerprint_stays_compatible(brain,tmp_path):
    vault,_=brain; git=git_fixture(tmp_path,vault)
    source,packet,result=fixture(brain); original=source.read_bytes()
    w.apply(packet,result); topic=vault/w.BASE/'Projects/example.md'
    claim=w.meta(topic.read_text())['wiki_claims'][0]
    assert claim['source_revision']=={'sha256':s.digest(original),
                                     'text_sha256':s.digest(w.normalized(original.decode('utf-8-sig')).encode()),
                                     'date':'2026-10-01'}
    assert w._claim_hash(claim)==w._claim_hash(result['pages'][0]['claims'][0])
    git('add','--','.'); git('commit','-m','Anchored supporting source')
    source.write_bytes(b'---\ndate: 2026-10-02\n---\n# Later revision\nThe owner replaced the prior source content.\n')
    git('add','--','.'); git('commit','-m','Later owner revision')
    report=w.lint()
    assert not report['issues'] and report['historical_evidence_count']==1
    assert report['verified_historical_evidence'][0]['source_sha256']==s.digest(original)


def test_identical_claim_keeps_origin_but_new_chunk_receipt_and_old_chunk_proof_hydrate(brain,tmp_path,monkeypatch):
    vault,state=brain; git=git_fixture(tmp_path,vault)
    source,_,result=fixture(brain)
    text=w.normalized(source.read_text()); text+='x'*(24000-len(text))
    source.write_bytes(text.encode()); first=w.prepare(); w.apply(first,result)
    topic=vault/w.BASE/'Projects/example.md'; old_claim=copy.deepcopy(w.meta(topic.read_text())['wiki_claims'][0])
    git('add','--','.'); git('commit','-m','First processed chunk and supporting anchor')
    # Extend outside the first canonical chunk. Its old proof remains valid.
    source.write_bytes((text+'y'*24000).encode())
    fresh=tmp_path/'fresh-state'; fresh.mkdir()
    monkeypatch.setattr(cfg,'STATE',fresh); monkeypatch.setattr(s,'STATE',fresh)
    assert w.status()['portable_receipt_parts']==1
    assert w.status()['pending_source_parts']==2
    # Now change the first chunk without removing its quoted sentence. It must
    # be processed again, even though the preserved claim keeps its old origin.
    changed=text.replace('Example Project','Example Updated')+'y'*24000
    source.write_bytes(changed.encode()); packet=w.prepare(); assert packet['sources'][0]['part']==1
    w.apply(packet,result)
    assert w.meta(topic.read_text())['wiki_claims']==[old_claim]
    assert len(list((vault/w.BASE/'Receipts').glob('*.md')))==2
    (fresh/'wiki-jobs.db').unlink()
    report=w.status()
    assert report['portable_receipt_parts']==1 and report['pending_source_parts']==2
    with w.db() as conn:
        assert conn.execute('SELECT processed_hash FROM sources WHERE path=?',(packet['sources'][0]['job_key'],)).fetchone()[0]==s.digest(source.read_bytes())
    assert not w.lint()['issues']


@pytest.mark.parametrize('field,value',[('sha256','f'*64),('text_sha256','e'*64),('text_sha256',[]),('date','2099-01-01')])
def test_tampered_explicit_origin_is_an_issue_even_when_git_contains_quote(brain,tmp_path,field,value):
    source,topic,_,old_sha,_,_,_=legacy_history(brain,tmp_path)
    info=w.meta(topic.read_text())
    claim=info['wiki_claims'][0]
    original=w._EvidenceHistory().source(claim['source'],old_sha,claim['quote'])
    assert original
    claim['source_revision']={key:original[key] for key in ('sha256','text_sha256','date')}
    info['wiki_claims'][0]['source_revision'][field]=value
    replace_meta(topic,info)
    report=w.lint()
    expected='malformed source revision metadata' if value==[] else 'evidence quote missing'
    assert any(issue['issue']==expected for issue in report['issues'])
    assert report['historical_evidence_count']==0


def test_receipt_attestation_without_actual_old_git_source_is_insufficient(brain,tmp_path):
    source,topic,result,old_sha,_,_,git=legacy_history(brain,tmp_path)
    # Remove the only reachable old objects by starting a history with the
    # current source and its legacy topic. Existing receipt remains intact.
    git('checkout','--orphan','without-original')
    git('add','--','.'); git('commit','-m','Current snapshot without historical source')
    report=w.lint()
    assert any(issue['issue']=='evidence quote missing' for issue in report['issues'])
    assert report['historical_evidence_count']==0
    assert list((s.VAULT/w.BASE/'Receipts').glob('*.md'))


def test_shallow_clone_missing_old_source_keeps_integrity_issue(brain,tmp_path,monkeypatch):
    source,topic,_,_,_,_,git=legacy_history(brain,tmp_path)
    clone=tmp_path/'shallow'
    git('clone','--depth=1','--no-local','--',s.VAULT.as_uri(),str(clone),cwd=tmp_path)
    monkeypatch.setattr(cfg,'VAULT',clone); monkeypatch.setattr(s,'VAULT',clone)
    assert (clone/'.git/shallow').exists()
    report=w.lint()
    assert any(issue['issue']=='evidence quote missing' for issue in report['issues'])
    assert report['historical_evidence_count']==0


def test_malformed_optional_old_anchor_never_crashes_history_lookup(brain,tmp_path):
    source,topic,result,old_sha,_,_,git=legacy_history(brain,tmp_path)
    info=w.meta(topic.read_text())
    info['wiki_claims'][0]['source_revision']={'sha256':old_sha,'text_sha256':[]}
    replace_meta(topic,info); git('add','--','.'); git('commit','-m','Malformed optional old anchor')
    info['wiki_claims'][0].pop('source_revision'); replace_meta(topic,info)
    # A malformed recent anchor is ignored; independently valid earlier source
    # and topic objects still establish the exact older revision.
    report=w.lint()
    assert not report['issues'] and report['historical_evidence_count']==1


def test_unknown_source_hash_is_not_downgraded_by_a_matching_quote(brain):
    source,packet,result=fixture(brain); w.apply(packet,result)
    topic=s.VAULT/w.BASE/'Projects/example.md'; info=w.meta(topic.read_text())
    info['source_hashes'][source.relative_to(s.VAULT).as_posix()]='f'*64
    info['wiki_claims'][0].pop('source_revision')
    replace_meta(topic,info)
    report=w.lint()
    assert {issue['issue'] for issue in report['issues']}=={'source missing or changed','source revision unverified'}


def test_history_budget_exhaustion_keeps_issue_instead_of_guessing(brain,tmp_path,monkeypatch):
    legacy_history(brain,tmp_path)
    original=w._EvidenceHistory.__init__
    def expired(self): original(self); self.deadline=w.time.monotonic()-1
    monkeypatch.setattr(w._EvidenceHistory,'__init__',expired)
    report=w.lint()
    assert any(issue['issue']=='evidence quote missing' for issue in report['issues'])
    assert report['history_verification']['git_commands']==0


def test_missing_promised_git_blob_cannot_fetch_even_without_new_lazy_fetch_variable(brain,tmp_path,monkeypatch):
    vault,_=brain; git=git_fixture(tmp_path,vault)
    source,_,_=fixture(brain)
    git('add','--','.'); git('commit','-m','Local-only promised source')
    git('config','uploadpack.allowFilter','true')
    clone=tmp_path/'partial-no-checkout'
    git('clone','--filter=blob:none','--no-checkout','--no-local','--',vault.as_uri(),str(clone),cwd=tmp_path)
    relative=source.relative_to(vault).as_posix()
    oid=git('rev-parse','HEAD:'+relative,cwd=clone).stdout.decode().strip()
    missing=lambda: git('rev-list','--objects','--missing=print','HEAD',cwd=clone).stdout.decode()
    assert '?'+oid in missing(), 'The real partial clone must actually omit the promised source blob.'
    config=(clone/'.git/config').read_bytes()
    packs=sorted(path.name for path in (clone/'.git/objects/pack').iterdir())
    monkeypatch.setattr(s,'VAULT',clone); monkeypatch.setattr(cfg,'VAULT',clone)
    original=w._GIT_POPEN; observed=[]
    def pre_lazy_fetch_variable_git(command,**kwargs):
        # Git 2.38 does not implement GIT_NO_LAZY_FETCH. Removing it on current
        # Git exercises the older supported transport guard without installing
        # another Git binary. Only a local file transport is ever available.
        env=dict(kwargs['env']); env.pop('GIT_NO_LAZY_FETCH',None)
        env.setdefault('GIT_ALLOW_PROTOCOL','file')
        observed.append(env['GIT_ALLOW_PROTOCOL']); kwargs['env']=env
        return original(command,**kwargs)
    monkeypatch.setattr(w,'_GIT_POPEN',pre_lazy_fetch_variable_git)
    assert w._EvidenceHistory()._git('cat-file','-s',oid) is None
    assert observed==['']
    assert '?'+oid in missing()
    assert (clone/'.git/config').read_bytes()==config
    assert sorted(path.name for path in (clone/'.git/objects/pack').iterdir())==packs
    # Positive control proves this object can lazy-fetch from the configured
    # promisor when local file transport is explicitly permitted.
    env=os.environ.copy(); env.pop('GIT_NO_LAZY_FETCH',None); env['GIT_ALLOW_PROTOCOL']='file'
    fetched=subprocess.run(['git','-C',str(clone),'cat-file','-s',oid],env=env,capture_output=True,check=True)
    assert int(fetched.stdout)==len(source.read_bytes())
    assert '?'+oid not in missing()
