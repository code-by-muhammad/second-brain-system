import hashlib,json,os,subprocess
from pathlib import Path
import pytest
import brain_settings as cfg, brain_store as s, brain_wiki as w
@pytest.fixture
def brain(tmp_path,monkeypatch):
    root=tmp_path/'runtime'; vault=tmp_path/'vault'; state=root/'state'
    state.mkdir(parents=True); vault.mkdir(); (root/'logs').mkdir()
    for module in (cfg,s):
        monkeypatch.setattr(module,'ROOT',root); monkeypatch.setattr(module,'STATE',state); monkeypatch.setattr(module,'VAULT',vault)
    monkeypatch.setattr(cfg,'ROLE','maintainer'); monkeypatch.setattr(cfg,'DEVICE_ID','test-host')
    return vault,state
@pytest.mark.parametrize('endpoint',['summary','status','attention-run'])
@pytest.mark.parametrize('shape',['credential','hidden','oversize','nul','surrogate','ordinary'])
def test_cached_worker_page_has_a_safe_locator_and_cache_stays_unchanged(brain,endpoint,shape):
    page={'credential':w.BASE+'Projects/sk-'+('SyntheticA'*3)+'.md','hidden':w.BASE+'.hidden/Page.md','oversize':w.BASE+'Projects/'+('a'*410)+'.md','nul':w.BASE+'Projects/x'+chr(0)+'.md','surrogate':w.BASE+'Projects/x'+chr(0xD800)+'.md','ordinary':w.BASE+'Index.md'}[shape]
    state=cfg.STATE/'wiki-worker-status.json'
    raw=json.dumps({'status':'attention','attention_required':True,'consecutive_failures':0,'retry_after':0,'reason':'WikiIntegrityError','integrity_page':page,'integrity_field':'path'}).encode()
    state.write_bytes(raw)
    value=w._worker_summary() if endpoint=='summary' else w.status()['worker'] if endpoint=='status' else w.run()
    if shape=='ordinary': assert value['integrity_page']==page
    else:
        assert value.get('integrity_page')==w.BASE+'Index.md'
        assert value.get('integrity_page_sha256')==s.digest(page.encode('utf-8',errors='surrogatepass'))
    assert state.read_bytes()==raw
def test_malformed_optional_receipt_filename_is_screened(brain):
    path=s.VAULT/w.BASE/'Receipts'/('sk-'+('SyntheticA'*3)+'.md'); path.parent.mkdir(parents=True); path.write_bytes(b'# Invalid synthetic receipt\n')
    before=path.read_bytes(); value=w.status()
    assert value['receipt_issues'] and path.name not in json.dumps(value)
    assert path.read_bytes()==before
def test_oversized_optional_receipt_is_read_with_a_finite_note_bound(brain,monkeypatch):
    path=s.VAULT/w.BASE/'Receipts'/'oversized.md'; path.parent.mkdir(parents=True); path.write_bytes(b'a'*2_000_001)
    original=Path.read_text
    def forbid(self,*args,**kwargs):
        assert self!=path,'Optional receipts must not use unbounded read_text.'
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',forbid)
    assert w.status()['receipt_issues'] and path.stat().st_size==2_000_001
def git(*args,input=None,canonical=False):
    env=os.environ.copy()
    if canonical:env['GIT_NO_REPLACE_OBJECTS']='1'
    return subprocess.run(['git','-C',str(s.VAULT),*args],input=input,check=True,capture_output=True,env=env).stdout
@pytest.mark.parametrize('scenario',['forged-body','canonical-old','no-replacement-control'])
def test_historical_proof_uses_canonical_objects_not_local_replacement_views(brain,monkeypatch,scenario):
    monkeypatch.setenv('GIT_NO_REPLACE_OBJECTS','0')
    original_popen=w._GIT_POPEN; calls=[]
    def inspect_git(*args,**kwargs):
        env=kwargs['env']; calls.append(env)
        assert env['GIT_NO_REPLACE_OBJECTS']=='1' and env['GIT_ALLOW_PROTOCOL']==''
        assert env['GIT_NO_LAZY_FETCH']=='1' and env['GIT_OPTIONAL_LOCKS']=='0'
        return original_popen(*args,**kwargs)
    monkeypatch.setattr(w,'_GIT_POPEN',inspect_git)
    source=s.VAULT/'01-Projects/Source.md';source.parent.mkdir(); old=b'# Original\nCanonical historic quotation is alpha lantern.\n'; fake=b'# Replacement\nUncommitted synthetic quotation is beta beacon.\n'
    source.write_bytes(old);git('init','-q');git('config','user.name','Synthetic');git('config','user.email','synthetic@example.invalid');git('config','core.autocrlf','false');git('add','.');git('commit','-qm','canonical historic source')
    old_oid=git('rev-parse','HEAD:01-Projects/Source.md').decode().strip(); fake_oid=git('hash-object','-w','--stdin',input=fake).decode().strip()
    if scenario!='no-replacement-control':git('replace',old_oid,fake_oid)
    assert git('cat-file','blob',old_oid,canonical=True)==old
    source.write_bytes(b'# Current\nA distinct current revision has gamma content.\n');git('add','.');git('commit','-qm','actual current source')
    raw=fake if scenario=='forged-body' else old
    quote='Uncommitted synthetic quotation is beta beacon.' if scenario=='forged-body' else 'Canonical historic quotation is alpha lantern.'
    proof=w._EvidenceHistory().source('01-Projects/Source.md',s.digest(raw),quote)
    assert calls
    if scenario=='forged-body':assert proof is None
    else:assert proof and proof['mode']=='git-history' and proof['git_blob']==old_oid

@pytest.mark.parametrize('shape',['credential-job-key','unchecked-extra','ordinary'])
def test_attention_pending_parts_are_screened_without_rewriting_cached_state(brain,shape):
    marker='sk-'+('SyntheticA'*3)
    row={'job_key':'01-Projects/Ordinary.md::part1','sha256':'a'*64}
    if shape=='credential-job-key':row['job_key']='01-Projects/'+marker+'.md::part1'
    if shape=='unchecked-extra':row['unchecked_details']=marker
    state=cfg.STATE/'wiki-worker-status.json'; raw=json.dumps({'status':'attention','attention_required':True,'consecutive_failures':0,'retry_after':0,'reason':'WikiIntegrityError','pending_parts':[row]}).encode(); state.write_bytes(raw)
    value=w.run()
    assert marker not in json.dumps(value)
    if shape=='ordinary':assert value['pending_parts']==[row]
    assert state.read_bytes()==raw

@pytest.mark.parametrize('field',['reason','batch_id'])
def test_attention_return_uses_controlled_cached_top_fields(brain,field):
    marker='sk-'+('SyntheticA'*3); value={'status':'attention','attention_required':True,'consecutive_failures':0,'retry_after':0,'reason':'WikiIntegrityError'}; value[field]=marker
    state=cfg.STATE/'wiki-worker-status.json';raw=json.dumps(value).encode();state.write_bytes(raw)
    assert marker not in json.dumps(w.run())
    assert state.read_bytes()==raw

@pytest.mark.parametrize('relative',['01-Projects/Outside.md',w.BASE+'../Escape.md',w.BASE+'Projects/CON.md',w.BASE+'Projects/x'+chr(9)+'.md',w.BASE+'Projects/x'+chr(127)+'.md'])
def test_cached_locator_rejects_noncanonical_paths(brain,relative):
    value=w._public_locator(relative)
    assert value=={'path':w.BASE+'Index.md','path_sha256':s.digest(relative.encode())}

@pytest.mark.parametrize('kind',['hidden','surrogate','nul','invalid-hash','zero-part','huge-part','noncanonical-part','max-part','eight-rows'])
def test_pending_parts_validate_only_bounded_canonical_fields(brain,kind):
    row={'job_key':'01-Projects/Ordinary.md::part1','sha256':'a'*64}
    if kind=='hidden': row['job_key']='01-Projects/.hidden.md::part1'
    if kind=='surrogate': row['job_key']='01-Projects/x'+chr(0xD800)+'.md::part1'
    if kind=='nul': row['job_key']='01-Projects/x'+chr(0)+'.md::part1'
    if kind=='invalid-hash': row['sha256']='unsafe'
    if kind=='zero-part': row['job_key']='01-Projects/Ordinary.md::part0'
    if kind=='huge-part': row['job_key']='01-Projects/Ordinary.md::part88'
    if kind=='noncanonical-part': row['job_key']='01-Projects/Ordinary.md::part01'
    if kind=='max-part': row['job_key']='01-Projects/Ordinary.md::part87'
    rows=[row]*9 if kind=='eight-rows' else [row]
    assert w._public_pending_parts(rows)==([row]*8 if kind=='eight-rows' else [row] if kind=='max-part' else [])

@pytest.mark.parametrize('invalid',['unsafe-name','oversize','invalid-utf8'])
def test_valid_receipt_survives_invalid_neighbor_with_bounded_reads(brain,monkeypatch,invalid):
    from test_wiki import fixture
    _,packet,output=fixture(brain); w.apply(packet,output)
    folder=s.VAULT/w.BASE/'Receipts'
    originals={p:p.read_bytes() for p in folder.glob('*.md')}
    bad=folder/(('sk-'+('SyntheticA'*3)) if invalid=='unsafe-name' else 'invalid')
    bad=bad.with_suffix('.md')
    raw=b'x'*2_000_001 if invalid=='oversize' else bytes([255]) if invalid=='invalid-utf8' else b'bad'
    bad.write_bytes(raw)
    with w.db() as conn: conn.execute('DELETE FROM sources')
    original=Path.open; read_sizes=[]
    class Reader:
        def __init__(self,stream): self.stream=stream
        def __enter__(self): self.stream.__enter__(); return self
        def __exit__(self,*args): return self.stream.__exit__(*args)
        def read(self,size=-1):
            assert size==2_000_001
            read_sizes.append(size); return self.stream.read(size)
    def guarded(path,*args,**kwargs):
        if path.parent==folder and args and args[0]=='rb':
            assert not (path==bad and invalid=='unsafe-name'),'Unsafe receipt must be rejected before reading.'
            return Reader(original(path,*args,**kwargs))
        return original(path,*args,**kwargs)
    with monkeypatch.context() as scope:
        scope.setattr(Path,'open',guarded)
        result=w._hydrate_receipts(w.inventory())
    assert result['verified_parts']==1 and len(result['issues'])==1 and read_sizes
    assert all(p.read_bytes()==data for p,data in originals.items()) and bad.read_bytes()==raw
    assert bad.name not in json.dumps(result) if invalid=='unsafe-name' else True

@pytest.mark.parametrize('prefix',['ghp_','gho_','sk-'])
def test_reason_class_shape_cannot_bypass_credential_screening(brain,prefix):
    marker=prefix+'SyntheticBoundaryMarker0123456789'
    path=cfg.STATE/'wiki-worker-status.json'
    raw=json.dumps({'status':'attention','attention_required':True,'reason':marker}).encode()
    path.write_bytes(raw)
    assert marker not in json.dumps(w._worker_summary())
    assert marker not in json.dumps(w.run())
    assert path.read_bytes()==raw

@pytest.mark.parametrize('encoding',['bom','crlf'])
def test_valid_optional_receipt_preserves_raw_encoding_and_hydrates(brain,encoding):
    from test_wiki import fixture
    _,packet,output=fixture(brain); w.apply(packet,output)
    receipt=next((s.VAULT/w.BASE/'Receipts').glob('*.md'))
    text=receipt.read_text(encoding='utf-8')
    raw=(bytes([239,187,191])+text.encode()) if encoding=='bom' else text.replace(chr(10),chr(13)+chr(10)).encode()
    receipt.write_bytes(raw)
    with w.db() as conn: conn.execute('DELETE FROM sources')
    result=w._hydrate_receipts(w.inventory())
    assert result['verified_parts']==1 and result['issues']==[]
    assert receipt.read_bytes()==raw
