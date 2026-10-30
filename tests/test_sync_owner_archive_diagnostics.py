"""Owner edit compatibility and raw-path-safe Git errors, using local Git only."""
import hashlib
import io
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import brain_git as syncer
import brain_store as store
from test_sync_archive_streaming import _pair, _archive, _git, _history, _install_filter, _intercept_first_size

ARCHIVES='02-Areas/AI-Memory/Wiki/Log-Archives/'
BLOCK=65536
CHARACTERS=2_000_000


def _refused(repo,path,raw,match=None):
    before=_git(repo,'rev-parse','HEAD')
    with pytest.raises(syncer.GitSyncError,match=match) as caught:
        syncer.sync(repo)
    assert _git(repo,'rev-parse','HEAD')==before
    assert not _git(repo,'diff','--cached','--name-only')
    assert path.read_bytes()==raw
    return str(caught.value)


@pytest.mark.parametrize('change',['ordinary-link-edit','credential-redaction'])
def test_ordinary_archive_owner_edit_and_redaction_push_to_second_clone(brain,tmp_path,change):
    a,b=_pair(tmp_path)
    initial=b'# Historical compilation log\n- [[02-Areas/AI-Memory/Wiki/Projects/old-topic]]\n'
    relative,path=_archive(a,initial)
    source=(a/'01-Projects/Initial.md').read_bytes()
    assert syncer.sync(a)=='synced'
    if change=='credential-redaction':
        blocked=initial+b'sk-'+b'SyntheticAlphaBetaGammaDelta'+b'\n'
        path.write_bytes(blocked)
        _refused(a,path,blocked,'redact before syncing')
    edited=initial.replace(b'old-topic',b'owner-renamed-topic')+b'Owner clarified the retained historical link.\n'
    path.write_bytes(edited)
    assert hashlib.sha256(edited).hexdigest()!=Path(relative).stem
    assert syncer.sync(a)=='synced'
    assert syncer.sync(b)=='synced' and (b/relative).read_bytes()==edited
    assert path.read_bytes()==edited and (a/'01-Projects/Initial.md').read_bytes()==source
    assert syncer.sync(a)=='synced' and syncer.sync(b)=='synced'
    assert not _git(a,'status','--porcelain') and not _git(b,'status','--porcelain')


@pytest.mark.parametrize('representation',['multibyte','crlf-at-block-edge','bom-counted'])
def test_ordinary_archive_character_capacity_matches_utf8_universal_newline_semantics(brain,tmp_path,monkeypatch,representation):
    a,b=_pair(tmp_path)
    relative,path=_archive(a,b'# Initial history\n')
    assert syncer.sync(a)=='synced'
    if representation=='multibyte':raw=('漢'*1_000_000).encode('utf-8')
    elif representation=='crlf-at-block-edge':raw=b'a'*(BLOCK-1)+b'\r\n'+b'b'*(CHARACTERS-BLOCK)
    else:raw=b'\xef\xbb\xbf'+b'x'*(CHARACTERS-1)
    assert len(raw)>CHARACTERS
    with io.TextIOWrapper(io.BytesIO(raw),encoding='utf-8',newline=None) as oracle:
        assert len(oracle.read())<=CHARACTERS
    path.write_bytes(raw)
    original_read_text=Path.read_text
    def no_whole_archive_text(self,*args,**kwargs):
        if self==path:pytest.fail('Canonical archive must remain bounded-stream validated.')
        return original_read_text(self,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',no_whole_archive_text)
    assert syncer.sync(a)=='synced' and syncer.sync(b)=='synced'
    assert path.read_bytes()==raw and (b/relative).read_bytes()==raw


@pytest.mark.parametrize('raw',[
    b'\xef\xbb\xbf# Owner history\r\nLone\rFinal\n',
    b'a'*(BLOCK-1)+b'\r\n'+b'b'*(BLOCK-1)+b'\r\n',
    b'a'*(BLOCK-1)+'€'.encode('utf-8')+b'\r\n'+b'\r',
],ids=['bom-lone-cr','crlf-block-boundary','utf8-block-boundary'])
def test_streamed_archive_character_count_matches_ordinary_reader_without_unbounded_reads(raw):
    class Bounded(io.BytesIO):
        def read(self,size=-1):
            assert 0<size<=BLOCK
            return super().read(size)
    with io.TextIOWrapper(io.BytesIO(raw),encoding='utf-8',newline=None) as oracle:expected=len(oracle.read())
    scanned=syncer._scan_archive(Bounded(raw),len(raw))
    assert scanned['characters']==expected
    assert scanned['raw_sha256']==hashlib.sha256(raw).hexdigest()
    assert scanned['lf_sha256']==hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()


def test_bom_counts_toward_ordinary_capacity_and_larger_owner_edit_remains_hash_bound(brain,tmp_path):
    a,_=_pair(tmp_path)
    _,path=_archive(a,b'# Initial history\n')
    assert syncer.sync(a)=='synced'
    raw=b'\xef\xbb\xbf'+b'x'*CHARACTERS
    path.write_bytes(raw)
    message=_refused(a,path,raw,'SHA256 filename')
    assert 'review the edit' in message


def test_larger_archive_stays_hash_bound_after_owner_edit_and_raw_guard_still_refuses_credentials(brain,tmp_path):
    a,_=_pair(tmp_path)
    raw=_history();_,path=_archive(a,raw)
    assert syncer.sync(a)=='synced'
    changed=raw+b'Owner revised oversized history.\n';path.write_bytes(changed)
    _refused(a,path,changed,'SHA256 filename')
    credential=raw+b'\nsecret: '+b'a'*16+b'\n';path.write_bytes(credential)
    _refused(a,path,credential,'redact before syncing')


def test_tiny_shared_archive_capacity_still_stops_before_content_reads(brain,tmp_path,monkeypatch):
    a,_=_pair(tmp_path)
    raw=b'# History\n'+b'a'*100;_,path=_archive(a,raw)
    original_open=Path.open;reads=[]
    class NoContentRead:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):self.stream.__enter__();return self
        def __exit__(self,*args):return self.stream.__exit__(*args)
        def __getattr__(self,name):return getattr(self.stream,name)
        def read(self,size=-1):
            reads.append(size)
            pytest.fail('The initial capacity bound must precede all archive content reads.')
    def no_archive_read(self,mode='r',*args,**kwargs):
        stream=original_open(self,mode,*args,**kwargs)
        return NoContentRead(stream) if self==path and mode=='rb' else stream
    with monkeypatch.context() as guard:
        guard.setattr(syncer,'ARCHIVE_BYTES_LIMIT',32)
        guard.setattr(Path,'open',no_archive_read)
        with pytest.raises(syncer.GitSyncError,match='sync limit'):syncer.sync(a)
    assert not reads and path.read_bytes()==raw and not _git(a,'diff','--cached','--name-only')


def test_edited_ordinary_archive_staged_credential_is_refused_and_unrelated_user_stage_preserved(brain,tmp_path,monkeypatch):
    a,_=_pair(tmp_path)
    relative,path=_archive(a,b'# Initial history\n')
    assert syncer.sync(a)=='synced'
    _install_filter(a,tmp_path,b'secret: '+b'a'*16+b'\n')
    raw=b'# Owner clarified archive\n'+b'ordinary retained history\n'*200
    path.write_bytes(raw)
    sibling=a/'01-Projects/Automatic.md';sibling.write_bytes(b'Automatically eligible sibling.\n')
    user_relative='01-Projects/User-staged.md'
    def user_stage(native,command,args,kwargs):
        (a/user_relative).write_bytes(b'Independent user staging.\n')
        native(['git','-C',str(a),'add','--',user_relative],check=True,capture_output=True)
    fired=_intercept_first_size(monkeypatch,user_stage)
    head=_git(a,'rev-parse','HEAD')
    with pytest.raises(syncer.GitSyncError,match='redact before syncing'):syncer.sync(a)
    assert fired() and _git(a,'rev-parse','HEAD')==head
    assert _git(a,'diff','--cached','--name-only').splitlines()==[user_relative]
    assert path.read_bytes()==raw and sibling.read_bytes()==b'Automatically eligible sibling.\n'
    assert relative not in _git(a,'diff','--cached','--name-only').splitlines()


@pytest.mark.parametrize('prefix',['sk-','ghp_','xoxb-'])
@pytest.mark.parametrize('branch',['utf8','nul','credential','size','read-error','linked'])
def test_every_git_error_screens_raw_credential_like_path_before_json_format(brain,tmp_path,monkeypatch,prefix,branch):
    a,_=_pair(tmp_path)
    marker=prefix+'SyntheticAlphaBetaGammaDelta'
    relative='01-Projects/Keys—'+marker+'.md'
    assert store.SECRET.search(relative)
    path=a/relative
    payload={'utf8':b'\xffinvalid','nul':b'ordinary\x00invalid','credential':b'secret: '+b'a'*16,'size':b'a'*(CHARACTERS+1),'read-error':b'ordinary','linked':b'ordinary'}[branch]
    path.write_bytes(payload)
    if branch=='read-error':
        original=Path.open
        def failed_read(self,*args,**kwargs):
            mode=args[0] if args else kwargs.get('mode','r')
            if self==path and mode=='r' and kwargs.get('encoding')=='utf-8':raise OSError('Synthetic unavailable note.')
            return original(self,*args,**kwargs)
        monkeypatch.setattr(Path,'open',failed_read)
    if branch=='linked':
        original=syncer.os.path.isjunction
        monkeypatch.setattr(syncer.os.path,'isjunction',lambda p:Path(p)==a/'01-Projects' or original(p))
    message=_refused(a,path,payload)
    assert marker not in message and relative not in message
    assert hashlib.sha256(relative.encode('utf-8','surrogatepass')).hexdigest() in message
    assert 'withheld locator' in message and not store.SECRET.search(message)


@pytest.mark.parametrize('relative',[
    '01-Projects/Keys—sk-SyntheticAlphaBetaGammaDelta.md',
    '01-Projects/Keys—ghp_SyntheticAlphaBetaGammaDelta.md',
    '01-Projects/Keys—xoxb-SyntheticAlphaBetaGammaDelta.md',
    '01-Projects/Bad\ud800name.md',
    '01-Projects/Bad\x00name.md',
    '01-Projects/'+'a'*401+'.md',
])
def test_archive_error_formatter_withholds_raw_unsafe_locator_with_exact_surrogatepass_hash(relative):
    message=str(syncer._archive_failure(relative,'read'))
    assert 'withheld locator' in message
    assert hashlib.sha256(relative.encode('utf-8','surrogatepass')).hexdigest() in message
    assert relative not in message and not store.SECRET.search(message)


@pytest.mark.parametrize('relative',['01-Projects/Ordinary.md','01-Projects/Ordinary — Résumé.md'])
def test_ordinary_git_error_locator_remains_actionable(brain,tmp_path,relative):
    a,_=_pair(tmp_path);path=a/relative;path.write_bytes(b'\xff')
    message=_refused(a,path,b'\xff')
    assert json.dumps(relative,ensure_ascii=True) in message and 'UTF-8' in message
    assert 'withheld locator' not in message


@pytest.mark.parametrize('prefix',['sk-','ghp_','xoxb-'])
def test_path_error_screen_does_not_add_a_new_sync_filename_policy(brain,tmp_path,prefix):
    a,b=_pair(tmp_path)
    relative='01-Projects/Keys—'+prefix+'SyntheticAlphaBetaGammaDelta.md'
    raw=b'Ordinary safe source content.\n';path=a/relative;path.write_bytes(raw)
    assert syncer.sync(a)=='synced' and syncer.sync(b)=='synced'
    assert path.read_bytes()==raw and (b/relative).read_bytes()==raw


def test_nonarchive_benign_clean_filter_retains_ordinary_sync_contract(brain,tmp_path):
    a,_=_pair(tmp_path)
    script=tmp_path/'normal-clean.py'
    script.write_text("import sys\nraw=sys.stdin.buffer.read()\nsys.stdout.buffer.write(raw.replace(b'Owner draft',b'Validated text'))\n",encoding='utf-8')
    command=shlex.quote(sys.executable.replace('\\','/'))+' '+shlex.quote(str(script).replace('\\','/'))
    _git(a,'config','filter.normal.clean',command)
    _git(a,'config','filter.normal.smudge','cat')
    (a/'.gitattributes').write_text('01-Projects/Filtered.md -text filter=normal\n',encoding='utf-8')
    _git(a,'add','.gitattributes');_git(a,'commit','-m','Synthetic normal filter');_git(a,'push')
    relative='01-Projects/Filtered.md';path=a/relative;raw=b'Owner draft\n';path.write_bytes(raw)
    assert syncer.sync(a)=='synced'
    assert path.read_bytes()==raw and _git(a,'show','HEAD:'+relative,raw=True)==b'Validated text\n'
