"""Known SafeLoader source types obey privacy bounds without changing originals."""
import base64,json
from pathlib import Path
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_remediation import fake_model
from test_wiki_compact_aggregate_budget import QUOTE,skip_output

TOKEN='ghp_'+'SyntheticKnownTypeC12345'*2
VALUE='SyntheticAssociationValue1234567890'

def escaped(value):return '"'+''.join('\\u'+format(ord(c),'04x') for c in value)+'"'
def binary(value):
    raw=value.encode('utf-8') if isinstance(value,str) else value
    return '!!binary "'+base64.b64encode(raw).decode('ascii')+'"'
def source(header):
    path=s.VAULT/'01-Projects/Example.md';path.parent.mkdir(parents=True)
    raw=('---\n'+header+'\n---\n# Example Project\n'+QUOTE+'\n').encode('utf-8');path.write_bytes(raw)
    return path,raw
def provider(monkeypatch,rows):
    output=skip_output();text=rows[0]['text'];output['dispositions'][0]['quote']=QUOTE if QUOTE in text else text[-40:].strip()
    return fake_model(monkeypatch,output)
def assert_attention(path,raw,rows,calls):
    report=w.run(1)
    assert not calls and report['status']=='attention' and report['integrity_field']=='content'
    assert report['integrity_page']=='01-Projects/Example.md'
    assert path.read_bytes()==raw and w.inventory()==rows
    assert w.status()['pending_source_parts']==len(rows)
    assert not list((cfg.STATE/'wiki/batches').glob('*/input.json'))
    rendered=json.dumps({'report':report,'cached':w._read_worker_state(),'public':w.status()['worker']})
    assert TOKEN not in rendered and VALUE not in rendered and '\\ud800' not in rendered

@pytest.mark.parametrize('header',[
    'annotation: !!set {'+escaped(TOKEN)+': null}',
    'annotation: !!set {'+binary(TOKEN)+': null}',
    'outer:\n  items:\n  - annotation: !!set {'+escaped(TOKEN)+': null}',
    'annotation: '+binary(TOKEN),
    binary(TOKEN)+': ordinary',
    'annotation: '+binary(b'\xff'+TOKEN.encode('ascii')),
    escaped('password')+': '+binary(VALUE),
    binary('password')+': '+escaped(VALUE),
    binary('access_token')+': '+binary(VALUE),
    'annotation: !!pairs [{'+escaped('password')+': '+binary(VALUE)+'}]',
    'annotation: !!omap [{'+binary('password')+': '+escaped(VALUE)+'}]',
    'first: &same !!set {'+escaped(TOKEN)+': null}\nsecond: *same',
    'padding: "'+'A'*25000+'"\nannotation: '+binary(TOKEN),
    'annotation: '+binary(b'opaque\x00annotation'),
    'annotation: !!set {"ordinary\\u0000annotation": null}',
    'annotation: !!set {"ordinary\\ud800annotation": null}',
],ids=['escaped-set','binary-set-member','nested-set','binary-value','binary-key','opaque-adjacent-token','string-key-binary-value','binary-key-string-value','binary-key-binary-value','pairs-association','omap-association','set-alias','after-first-chunk','binary-NUL','set-NUL','set-surrogate'])
def test_known_constructed_source_metadata_requires_safe_attention_before_provider(brain,monkeypatch,header):
    path,raw=source(header);assert not s.SECRET.search(raw.decode('utf-8'))
    decoded=w.meta(raw.decode('utf-8'));assert decoded
    rows=w.inventory();assert rows
    calls=provider(monkeypatch,rows)
    assert_attention(path,raw,rows,calls)

@pytest.mark.parametrize('header',[
    'annotation: !!set {ordinary: null, "Discuss API key setup": null}',
    'annotation: '+binary('Ordinary annotation'),
    'annotation: '+binary(b'\xff\xfe\x80ordinary annotation'),
    binary('ordinary-key')+': '+binary('ordinary-value'),
    escaped('password')+': '+binary('rotate safely'),
    'first: &same !!set {ordinary: null}\nsecond: *same\nthird: [*same, *same]',
    'annotation: !!pairs [{'+binary('ordinary-key')+': '+binary('ordinary-value')+'}]',
    'annotation: !!omap [{'+escaped('api_key')+': '+binary('short')+'}]\ndate: 2026-10-01',
],ids=['set','ASCII-binary','opaque-non-UTF8-binary','binary-key-value','benign-association','fitting-set-alias','pairs','omap-date'])
def test_benign_known_source_types_preserve_compilation_and_original_bytes(brain,monkeypatch,header):
    path,raw=source(header);assert not s.SECRET.search(raw.decode('utf-8')) and w.meta(raw.decode('utf-8'))
    rows=w.inventory();calls=provider(monkeypatch,rows);report=w.run(1)
    assert report['status']=='complete' and len(calls)==1 and path.read_bytes()==raw

@pytest.mark.parametrize('annotation',[ '!!set {'+escaped(TOKEN)+': null}',binary(TOKEN)],ids=['set','binary'])
def test_legacy_unused_packet_rechecks_known_constructed_types_without_rewriting_input(brain,monkeypatch,annotation):
    path,raw=source('annotation: '+annotation)
    with monkeypatch.context() as legacy:
        legacy.setattr(w,'_validate_source_context',lambda packet:None)
        packet=w.prepare(1)
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];saved=(folder/'input.json').read_bytes()
    calls=provider(monkeypatch,w.inventory());report=w.run(1)
    assert not calls and report['status']=='attention' and report['integrity_field']=='content'
    assert report['integrity_page']=='01-Projects/Example.md'
    assert path.read_bytes()==raw and (folder/'input.json').read_bytes()==saved
    assert w.status()['pending_source_parts']==1

@pytest.mark.parametrize('sensitive',[True,False],ids=['unsafe','benign'])
def test_already_constructed_frozenset_uses_the_same_source_preflight(brain,monkeypatch,sensitive):
    path,raw=source('annotation: ordinary');old_meta=w.meta
    def known_reader(text,*args,**kwargs):
        value=old_meta(text,*args,**kwargs)
        if QUOTE in text:value['annotation']=frozenset([TOKEN if sensitive else 'ordinary annotation'])
        return value
    monkeypatch.setattr(w,'meta',known_reader)
    rows=w.inventory();calls=provider(monkeypatch,rows);report=w.run(1)
    assert path.read_bytes()==raw
    if sensitive:assert not calls and report['status']=='attention' and report['integrity_field']=='content'
    else:assert report['status']=='complete' and len(calls)==1

@pytest.mark.parametrize('kind',['nodes','depth','aliased-binary-text'])
def test_known_constructed_types_share_finite_source_metadata_budgets(brain,monkeypatch,kind):
    if kind=='nodes':header='annotation: !!set {'+', '.join(str(i)+': null' for i in range(100000))+'}'
    elif kind=='depth':header='annotation: '+'['*127+'!!set {ordinary: null}'+']'*127
    else:header='a: &same '+binary(b'A'*700000)+'\nb: *same\nc: *same'
    path,raw=source(header);assert len(raw)<2_000_000 and not s.SECRET.search(raw.decode('utf-8'))
    decoded=w.meta(raw.decode('utf-8'));assert decoded
    if kind=='nodes':assert len(decoded['annotation'])==100000 and isinstance(decoded['annotation'],set)
    elif kind=='depth':
        value=decoded['annotation']
        for _ in range(127):value=value[0]
        assert isinstance(value,set)
    else:assert decoded['a'] is decoded['b'] is decoded['c'] and len(decoded['a'])==700000
    rows=w.inventory();calls=provider(monkeypatch,rows)
    assert_attention(path,raw,rows,calls)

@pytest.mark.parametrize('header',[
    'outer: &same [!!set {ordinary: null}, *same]',
    'outer: &same {annotation: !!set {ordinary: null}, cycle: *same}',
    'annotation: "ordinary\\u0000annotation"',
    'annotation: "ordinary\\ud800annotation"',
],ids=['recursive-sequence','recursive-mapping','existing-string-NUL','existing-string-surrogate'])
def test_existing_source_cycle_and_string_integrity_controls_stay_enforced(brain,monkeypatch,header):
    path,raw=source(header);assert w.meta(raw.decode('utf-8'))
    rows=w.inventory();calls=provider(monkeypatch,rows)
    assert_attention(path,raw,rows,calls)
