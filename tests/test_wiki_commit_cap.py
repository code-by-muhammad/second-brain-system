"""Real Git log truncation diagnostic; blob caches isolate the commit budget."""
import subprocess
import pytest
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_history import git_fixture

def history65(brain,tmp_path,count=65):
    source,packet,result=fixture(brain);w.apply(packet,result)
    git=git_fixture(tmp_path,s.VAULT);relative=source.relative_to(s.VAULT).as_posix();original=source.read_bytes()
    quote=result['pages'][0]['claims'][0]['quote'];stream=bytearray();bodies=[]
    for i in range(count):
        body=original if i==0 else original.replace(quote.encode(),f'A different synthetic owner revision {i}.'.encode())
        bodies.append(body);message=f'Synthetic history revision {i}'.encode()
        stream.extend(f'commit refs/heads/main\ncommitter History Test <history@example.invalid> {1700000000+i} +0000\ndata {len(message)}\n'.encode()+message+b'\n')
        stream.extend(f'M 100644 inline {relative}\ndata {len(body)}\n'.encode()+body+b'\n\n')
    subprocess.run(['git','-C',str(s.VAULT),'fast-import','--quiet'],input=bytes(stream),capture_output=True,check=True)
    commits=git('log','--format=%H','HEAD','--',relative).stdout.decode().splitlines()
    assert len(commits)==count and git('show',commits[-1]+':'+relative).stdout==original
    source.write_bytes(bodies[-1])
    return source,relative,original,commits

def seed_unmatching_blob_cache(history,relative,commits):
    # These64 negative blobs are already-examined cache entries. The log command
    # itself remains real; no fake histories, proofs or widened product budget.
    history.blobs.update({(commit,relative):None for commit in commits[:64]})

def test_actual_65_commit_lint_negative_reports_incomplete(brain,tmp_path,monkeypatch):
    source,relative,original,commits=history65(brain,tmp_path)
    init=w._EvidenceHistory.__init__
    def seeded(self):init(self);seed_unmatching_blob_cache(self,relative,commits)
    monkeypatch.setattr(w._EvidenceHistory,'__init__',seeded)
    before=source.read_bytes();report=w.lint()
    issues=[issue for issue in report['issues'] if issue.get('source')==relative]
    assert issues and all(issue.get('history_incomplete')=='commits' for issue in issues)
    assert report['history_verification']['incomplete_reason']=='commits'
    assert source.read_bytes()==before

def test_cached_log_replays_truncation_for_a_different_check(brain,tmp_path):
    _,relative,original,commits=history65(brain,tmp_path)
    history=w._EvidenceHistory();seed_unmatching_blob_cache(history,relative,commits)
    for quote in ('first absent quote','second absent quote'):
        serial=history.failure_serial
        assert history.source(relative,s.digest(original),quote) is None
        assert history.incomplete_since(serial)=='commits'
    assert len(history.logs[relative])==64 and history.commands==1

def test_positive_proof_inside_retained64_is_still_usable(brain,tmp_path):
    source,relative,_,commits=history65(brain,tmp_path)
    raw=source.read_bytes();source.write_bytes(b'# Synthetic uncommitted replacement\n')
    history=w._EvidenceHistory();serial=history.failure_serial
    proof=history.source(relative,s.digest(raw))
    assert proof and proof['mode']=='git-history' and proof['git_commit']==commits[0]
    assert proof['git_blob']!='a'*40 and len(history.logs[relative])==64
    assert history.incomplete_since(serial) is None and history.commands==4

def test_exactly64_commits_is_complete_for_a_negative(brain,tmp_path):
    _,relative,original,commits=history65(brain,tmp_path,count=64)
    history=w._EvidenceHistory();seed_unmatching_blob_cache(history,relative,commits)
    serial=history.failure_serial
    assert history.source(relative,s.digest(original),'a genuinely absent quote') is None
    assert history.incomplete_since(serial) is None and history.commands==1
