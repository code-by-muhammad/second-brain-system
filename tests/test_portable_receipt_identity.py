"""Shared completion proofs must survive Git checkout newline conversion."""
import json
import os
from pathlib import Path
import subprocess

import pytest

import brain_settings as cfg
import brain_store as store
import brain_wiki as wiki


SOURCE='01-Projects/Portable.md'
QUOTE='A prototype was demonstrated. Production deployment remains planned.'
TEXT='---\ndate: 2026-10-01\n---\n# Portable source\n\n'+QUOTE+'\n'


def git_runner(tmp_path):
    config=tmp_path/'isolated.gitconfig'; config.write_bytes(b'')
    hooks=tmp_path/'empty-hooks'; hooks.mkdir()
    env=os.environ.copy()
    env.update(GIT_CONFIG_GLOBAL=str(config),GIT_CONFIG_NOSYSTEM='1')
    def git(directory,*args):
        return subprocess.run(['git','-c','user.name=Portable Test','-c','user.email=portable@example.invalid',
                               '-c','commit.gpgsign=false','-c','core.hooksPath='+str(hooks),*args],
                              cwd=directory,env=env,capture_output=True,check=True)
    return git


def choose_vault(monkeypatch,vault):
    monkeypatch.setattr(store,'VAULT',vault)
    monkeypatch.setattr(cfg,'VAULT',vault)


def result_for(row):
    return {'pages':[{'category':'Projects','slug':'portable','title':'Portable source','related':[],
                     'claims':[{'text':'The source distinguishes a demonstrated prototype from planned deployment.',
                                'source':SOURCE,'quote':QUOTE,'status':'historical'}]}],
            'dispositions':[{'source':SOURCE,'part':row['part'],'status':'published','reason':'','quote':''}]}


def compile_one(vault):
    packet=wiki.prepare()
    result=result_for(packet['sources'][0])
    wiki.apply(packet,result)
    return packet


def test_real_git_crlf_to_lf_clone_hydrates_verified_completion(brain,tmp_path,monkeypatch):
    git=git_runner(tmp_path)
    origin=tmp_path/'origin'; origin.mkdir()
    source=origin/SOURCE; source.parent.mkdir(); source.write_bytes(TEXT.encode())
    git(origin,'init','--initial-branch=main')
    git(origin,'add','--',SOURCE); git(origin,'commit','-m','Synthetic source')
    windows=tmp_path/'windows-checkout'
    git(tmp_path,'clone','--no-local','--config','core.autocrlf=true','--',str(origin),str(windows))
    choose_vault(monkeypatch,windows)
    windows_raw=(windows/SOURCE).read_bytes()
    assert b'\r\n' in windows_raw
    packet=compile_one(windows)
    receipt=next((windows/wiki.BASE/'Receipts').glob('*.md'))
    original_receipt=wiki.meta(receipt.read_text(encoding='utf-8'))['wiki_receipt']
    assert original_receipt['parts'][0]['sha256']==store.digest(windows_raw)
    git(windows,'add','--','02-Areas')
    git(windows,'commit','-m','Verified synthetic wiki receipts')
    linux=tmp_path/'linux-checkout'
    git(tmp_path,'clone','--no-local','--config','core.autocrlf=false','--',str(windows),str(linux))
    linux_raw=(linux/SOURCE).read_bytes()
    assert b'\r\n' not in linux_raw
    assert windows_raw!=linux_raw
    assert wiki.normalized(windows_raw.decode('utf-8-sig'))==wiki.normalized(linux_raw.decode('utf-8-sig'))
    choose_vault(monkeypatch,linux)
    fresh_state=tmp_path/'fresh-runtime/state'; fresh_state.mkdir(parents=True)
    monkeypatch.setattr(cfg,'STATE',fresh_state); monkeypatch.setattr(store,'STATE',fresh_state)
    monkeypatch.setattr(cfg,'ROLE','capture')
    row=wiki.inventory()[0]
    assert wiki._source_identity(row)['part_sha256']==original_receipt['parts'][0]['part_sha256']
    report=wiki.status()
    print(json.dumps({'windows_raw_sha256':store.digest(windows_raw),'linux_raw_sha256':store.digest(linux_raw),
                      'same_part_sha256':wiki._source_identity(row)['part_sha256'],
                      'portable_receipt_parts':report['portable_receipt_parts'],
                      'pending_source_parts':report['pending_source_parts'],
                      'lint_issues':wiki.lint()['issues']}))
    assert report['receipt_issue_count']==0
    assert report['role']=='capture'
    assert report['portable_receipt_parts']==1
    assert report['pending_source_parts']==0
    assert not wiki.lint()['issues']
    with wiki.db() as conn:
        assert conn.execute('SELECT processed_hash FROM sources WHERE path=?',(packet['sources'][0]['job_key'],)).fetchone()[0]==store.digest(linux_raw)


@pytest.mark.parametrize('newline,bom',[('\r\n',False),('\r',False),('\n',True),('\r\n',True)])
def test_equivalent_source_eol_and_bom_reuses_proof_but_binds_local_raw_hash(brain,monkeypatch,newline,bom):
    vault,state=brain
    source=vault/SOURCE; source.parent.mkdir(); source.write_bytes(TEXT.encode())
    packet=compile_one(vault)
    source.write_bytes((('\ufeff' if bom else '')+TEXT.replace('\n',newline)).encode())
    current=store.digest(source.read_bytes())
    assert current!=packet['sources'][0]['sha256']
    (state/'wiki-jobs.db').unlink()
    report=wiki.status()
    assert report['portable_receipt_parts']==1 and report['pending_source_parts']==0
    with wiki.db() as conn:
        assert conn.execute('SELECT processed_hash FROM sources WHERE path=?',(packet['sources'][0]['job_key'],)).fetchone()[0]==current


def test_unchanged_first_chunk_proof_survives_append_but_new_parts_stay_pending(brain):
    vault,state=brain
    source=vault/SOURCE; source.parent.mkdir()
    text=TEXT+'x'*(24000-len(TEXT)); source.write_bytes(text.encode())
    packet=compile_one(vault)
    assert packet['sources'][0]['parts']==2
    source.write_bytes((text+'y'*24000).encode())
    (state/'wiki-jobs.db').unlink()
    current=wiki.inventory(); assert len(current)==3
    report=wiki.status()
    assert report['portable_receipt_parts']==1 and report['pending_source_parts']==2
    with wiki.db() as conn:
        completed={row['path']:row['processed_hash'] for row in conn.execute('SELECT * FROM sources')}
    assert completed=={current[0]['job_key']:current[0]['sha256']}


def test_actual_chunk_edit_stays_pending_even_when_quoted_sentence_survives(brain):
    vault,state=brain
    source=vault/SOURCE; source.parent.mkdir(); source.write_bytes(TEXT.encode())
    compile_one(vault)
    source.write_bytes((TEXT+'\nA new historical requirement remains unreviewed.\n').encode())
    assert QUOTE in source.read_text()
    (state/'wiki-jobs.db').unlink()
    report=wiki.status()
    assert report['portable_receipt_parts']==0 and report['pending_source_parts']==1


@pytest.mark.parametrize('mutation',['receipt-hash','part-hash','claim-fingerprint','topic-claim'])
def test_portable_identity_keeps_tamper_and_exact_claim_guards(brain,mutation):
    vault,state=brain
    source=vault/SOURCE; source.parent.mkdir(); source.write_bytes(TEXT.encode())
    packet=compile_one(vault)
    receipt=vault/wiki.BASE/'Receipts'/f'{packet["batch_id"]}.md'
    info=wiki.meta(receipt.read_text(encoding='utf-8'))
    entry=info['wiki_receipt']['parts'][0]
    if mutation=='receipt-hash': info['receipt_sha256']='0'*64
    elif mutation=='part-hash': entry['part_sha256']='0'*64
    elif mutation=='claim-fingerprint': entry['proofs'][0]['claims']=['0'*64]
    elif mutation=='topic-claim':
        topic=vault/wiki.BASE/'Projects/portable.md'; topic_info=wiki.meta(topic.read_text(encoding='utf-8'))
        topic_info['wiki_claims'][0]['text']='An owner changed the derived statement after this publication.'
        topic.write_text(store.note_text(topic_info,'# Changed synthetic derived page'),encoding='utf-8')
    if mutation!='receipt-hash': info['receipt_sha256']=wiki._json_hash(info['wiki_receipt'])
    receipt.write_text(store.note_text(info,'# Synthetic proof guard fixture'),encoding='utf-8')
    # Simultaneously exercise the cross-platform path; valid source content alone
    # must never override a damaged receipt or a missing exact claim fingerprint.
    source.write_bytes(('\ufeff'+TEXT.replace('\n','\r\n')).encode())
    (state/'wiki-jobs.db').unlink()
    report=wiki.status()
    assert report['portable_receipt_parts']==0 and report['pending_source_parts']==1
    assert report['receipt_issue_count']==(1 if mutation=='receipt-hash' else 0)


def test_portable_identity_does_not_relax_raw_source_cas_during_publication(brain):
    vault,_=brain
    source=vault/SOURCE; source.parent.mkdir(); source.write_bytes(TEXT.encode())
    packet=wiki.prepare()
    source.write_bytes(TEXT.replace('\n','\r\n').encode())
    with pytest.raises(wiki.WikiTransientError,match='Source changed'):
        wiki.apply(packet,result_for(packet['sources'][0]))
    assert not (vault/wiki.BASE/'Receipts').exists()


@pytest.mark.parametrize('short',[False,True])
def test_eol_or_bom_equivalent_explicit_skip_is_portable(brain,short):
    vault,state=brain
    source=vault/SOURCE; source.parent.mkdir()
    text='' if short else TEXT; source.write_bytes(text.encode())
    packet=wiki.prepare(); row=packet['sources'][0]
    reason=wiki.SHORT_PART_REASON if short else 'This administrative fixture has been explicitly reviewed without a reusable topic.'
    result={'pages':[],'dispositions':[{'source':SOURCE,'part':row['part'],'status':'skipped',
                                      'reason':reason,'quote':'' if short else QUOTE}]}
    wiki.apply(packet,result)
    source.write_bytes(('\ufeff'+text.replace('\n','\r\n')).encode())
    (state/'wiki-jobs.db').unlink()
    report=wiki.status()
    assert report['portable_receipt_parts']==1 and report['pending_source_parts']==0
