"""History capacity is not missing evidence; exact Git proof stays required."""
import copy
import subprocess

import pytest

import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_history import git_fixture, legacy_history, replace_meta
from test_wiki_history_budget import history_fixture


def test_slow_page_scan_cannot_spend_the_historical_verification_budget(brain,tmp_path,monkeypatch):
    source,topic,_,old_sha,_,_,_=legacy_history(brain,tmp_path)
    before=(source.read_bytes(),topic.read_bytes());clock=[w.time.monotonic()]
    original=w.pages
    def slow_pages(*args,**kwargs):
        result=original(*args,**kwargs);clock[0]+=16;return result
    monkeypatch.setattr(w.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(w,'pages',slow_pages)
    report=w.lint()
    assert not report['issues'] and report['historical_evidence_count']==1
    assert report['verified_historical_evidence'][0]['source_sha256']==old_sha
    assert (source.read_bytes(),topic.read_bytes())==before


def test_publication_page_scan_precedes_history_budget(brain,monkeypatch):
    source,packet,result=fixture(brain);clock=[w.time.monotonic()];observed=[]
    original_pages=w._checked_pages;original_render=w._render_topic
    def slow_pages(*args,**kwargs):
        value=original_pages(*args,**kwargs);clock[0]+=16;return value
    def render(*args,**kwargs):
        history=kwargs.get('history') or args[4]
        observed.append(history._active());return original_render(*args,**kwargs)
    monkeypatch.setattr(w.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(w,'_checked_pages',slow_pages);monkeypatch.setattr(w,'_render_topic',render)
    w.apply(packet,result)
    assert observed and all(observed)


def test_legacy_first_exact_proof_does_not_wait_for_unneeded_history_tail(brain,tmp_path,monkeypatch):
    page,claims,_=history_fixture(brain,tmp_path,claims=2,revisions=6)
    control=w._EvidenceHistory();versions=list(control._versions(page['path']))
    old_sha=versions[1]['_meta']['source_hashes'][claims[0]['source']]
    expected=control.source(claims[0]['source'],old_sha,claims[0]['quote']);assert expected
    original=w._EvidenceHistory._versions;visited=[]
    def delayed_tail(self,relative):
        for index,info in enumerate(original(self,relative)):
            if relative==page['path']:
                visited.append(index)
                if index>=2:self.deadline=w.time.monotonic()-1
            yield info
    monkeypatch.setattr(w._EvidenceHistory,'_versions',delayed_tail)
    history=w._EvidenceHistory();proof=history.claim(page,claims[0])
    assert proof==expected and visited==[0,1]
    assert not history.topic_origins  # No partial origin walk is negative evidence.
    history.deadline=w.time.monotonic()-1
    assert history.claim(page,claims[0])==proof


def test_positive_legacy_cache_does_not_hide_new_current_proof(brain,tmp_path):
    page,claims,_=history_fixture(brain,tmp_path,claims=1,revisions=2)
    history=w._EvidenceHistory();assert history.claim(page,claims[0])['mode']=='git-history'
    source=s.VAULT/claims[0]['source']
    source.write_bytes(('---\ndate: 2026-10-03\n---\n# Restored exact sentence\n'+claims[0]['quote']+'\n').encode())
    page['meta']['source_hashes'][claims[0]['source']]=s.digest(source.read_bytes())
    history.current.clear();history.current_bytes=0
    proof=history.claim(page,claims[0])
    assert proof['mode']=='current' and proof['date']=='2026-10-03'


@pytest.mark.parametrize('unborn',[False,True],ids=['no-repository','unborn-head'])
def test_unavailable_git_is_labeled_and_failing_log_is_not_repeated(brain,tmp_path,unborn):
    source,packet,result=fixture(brain)
    if unborn:git_fixture(tmp_path,s.VAULT)
    row=packet['sources'][0];history=w._EvidenceHistory()
    for _ in range(8):
        serial=history.failure_serial
        assert history.source(row['path'],'f'*64,result['pages'][0]['claims'][0]['quote']) is None
        assert history.incomplete_since(serial)=='git-error'
    assert history.commands==1


@pytest.mark.parametrize('remaining,reason',[(15,'git-timeout'),(.5,'deadline')])
def test_git_timeout_reason_and_retry_policy(brain,tmp_path,monkeypatch,remaining,reason):
    page,claims,_=history_fixture(brain,tmp_path,claims=1,revisions=2)
    control=w._EvidenceHistory();old=list(control._versions(page['path']))[1]
    old_sha=old['_meta']['source_hashes'][claims[0]['source']]
    original=w._GIT_POPEN;calls=[]
    class Timeout:
        returncode=0
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def communicate(self,timeout=None):
            if timeout is not None:raise subprocess.TimeoutExpired('synthetic Git',timeout)
            return b'',None
        def kill(self):pass
    def first_timeout(*args,**kwargs):
        calls.append(True)
        return Timeout() if len(calls)==1 else original(*args,**kwargs)
    monkeypatch.setattr(w,'_GIT_POPEN',first_timeout)
    now=w.time.monotonic();monkeypatch.setattr(w.time,'monotonic',lambda:now)
    history=w._EvidenceHistory();history.deadline=w.time.monotonic()+remaining
    serial=history.failure_serial
    assert history.source(claims[0]['source'],old_sha,claims[0]['quote']) is None
    assert history.incomplete_since(serial)==reason
    if reason=='git-timeout':
        proof=history.source(claims[0]['source'],old_sha,claims[0]['quote'])
        assert proof and proof['mode']=='git-history'
    else:
        serial=history.failure_serial
        assert history.source(claims[0]['source'],old_sha,claims[0]['quote']) is None
        assert history.incomplete_since(serial)=='deadline' and len(calls)==1


@pytest.mark.parametrize('limit,reason',[(256,'commands'),(16_000_000,'blob-bytes')])
def test_capacity_failure_replayed_without_claiming_evidence_absent(brain,tmp_path,limit,reason):
    page,claims,_=history_fixture(brain,tmp_path,claims=1,revisions=2)
    history=w._EvidenceHistory()
    if reason=='commands':history.commands=limit
    else:history.bytes=limit
    for _ in range(2):
        serial=history.failure_serial
        assert history.claim(page,claims[0]) is None
        assert history.incomplete_since(serial)==reason
    assert history.commands<=256 and history.bytes<=16_000_000


def test_oversized_actual_git_blob_remains_unverified_and_labeled(brain,tmp_path):
    source,packet,result=fixture(brain);git=git_fixture(tmp_path,s.VAULT)
    original=source.read_bytes()+b'x'*2_000_001;source.write_bytes(original)
    git('add','--','.');git('commit','-m','Oversized synthetic historical source')
    source.write_bytes(b'# Current owner source\n')
    git('add','--','.');git('commit','-m','Small current source')
    history=w._EvidenceHistory();serial=history.failure_serial
    assert history.source(packet['sources'][0]['path'],s.digest(original),result['pages'][0]['claims'][0]['quote']) is None
    assert history.incomplete_since(serial)=='blob-size' and history.bytes<=16_000_000


def test_lint_deadline_outside_git_is_local_to_affected_claim(brain,tmp_path,monkeypatch):
    source,topic,_,_,_,_,_=legacy_history(brain,tmp_path)
    info=w.meta(topic.read_text());claim=copy.deepcopy(info['wiki_claims'][0])
    current=w._source_bytes(source.read_bytes())
    claim.update(text='A deliberately wrong current-source date remains unverified.',quote='A later owner revision describes a different review outcome.',
                 source_revision={'sha256':current['raw_sha256'],'text_sha256':current['text_sha256'],'date':'2099-01-01'})
    info['wiki_claims'].append(claim);replace_meta(topic,info)
    original=w._EvidenceHistory.__init__
    def expired(self):original(self);self.deadline=w.time.monotonic()-1
    monkeypatch.setattr(w._EvidenceHistory,'__init__',expired)
    report=w.lint();issues={issue['claim_index']:issue for issue in report['issues'] if 'claim_index' in issue}
    assert issues[0]['history_incomplete']=='deadline'
    assert 'history_incomplete' not in issues[1]
    assert report['history_verification']['incomplete_reason']=='deadline'


def test_later_git_error_cannot_cache_an_earlier_retryable_timeout(brain,tmp_path,monkeypatch):
    page,claims,_=history_fixture(brain,tmp_path,claims=1,revisions=2)
    control=w._EvidenceHistory();old=list(control._versions(page['path']))[1]
    old_sha=old['_meta']['source_hashes'][claims[0]['source']]
    actual=next(info for info in control._versions(claims[0]['source']) if old_sha in info['variants'])
    calls=[]
    def versions(self,relative):
        calls.append(relative)
        if len(calls)==1:
            self._incomplete('git-timeout');self._incomplete('git-error');return
        yield actual
    monkeypatch.setattr(w._EvidenceHistory,'_versions',versions)
    history=w._EvidenceHistory()
    assert history.source(claims[0]['source'],old_sha,claims[0]['quote']) is None
    assert not history.results
    proof=history.source(claims[0]['source'],old_sha,claims[0]['quote'])
    assert proof and proof['mode']=='git-history' and len(calls)==2


def test_renderer_keeps_owner_review_warning_with_incomplete_reason(brain):
    source,packet,result=fixture(brain);w.apply(packet,result)
    topic=s.VAULT/w.BASE/'Projects/example.md';info=w.meta(topic.read_text())
    info['wiki_claims'][0]['source_revision']['sha256']='f'*64
    expected=copy.deepcopy(info['wiki_claims'][0]['source_revision'])
    replace_meta(topic,info)
    another=source.with_name('Another.md');another.write_bytes(source.read_bytes())
    packet=w.prepare();result=copy.deepcopy(result)
    result['pages'][0]['claims'][0]['source']=another.relative_to(s.VAULT).as_posix()
    w.apply(packet,result)
    assert 'evidence requires review; historical verification incomplete (git-error)' in topic.read_text()
    assert w.meta(topic.read_text())['wiki_claims'][0]['source_revision']==expected
