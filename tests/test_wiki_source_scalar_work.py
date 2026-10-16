"""Source-only numeric compatibility and bounded detector work over real YAML."""
import base64,json,math,re
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_remediation import fake_model
from test_wiki_compact_aggregate_budget import QUOTE,skip_output

def binary(value):
    raw=value.encode('utf-8') if isinstance(value,str) else value
    return '!!binary "'+base64.b64encode(raw).decode('ascii')+'"'
def escaped(value):return '"'+''.join('\\u'+format(ord(c),'04x') for c in value)+'"'
def original(header):
    path=s.VAULT/'01-Projects/Example.md';path.parent.mkdir(parents=True)
    raw=('---\n'+header+'\n---\n# Example Project\n'+QUOTE+'\n').encode('utf-8');assert len(raw)<2_000_000 and not s.SECRET.search(raw.decode('utf-8'));path.write_bytes(raw)
    return path,raw
def calls_for(monkeypatch,rows):
    output=skip_output();text=rows[0]['text'];output['dispositions'][0]['quote']=QUOTE if QUOTE in text else text[-40:].strip()
    return fake_model(monkeypatch,output)

@pytest.mark.parametrize('header',[
    'limit: .inf','limit: -.inf','limit: .nan','limit: !!float +.Inf','"rate": .NaN',
    binary('rate')+': .inf','outer: {limit: -.inf}',
    'annotation: !!pairs [{limit: .nan}]','annotation: !!omap [{'+binary('limit')+': .inf}]',
    '!!float .nan: ordinary','limits: !!set {.inf: null, -.inf: null, .nan: null}',
    'date: 2026-10-01\nlimit: 12\nenabled: true\nempty: null',
],ids=['positive-infinity','negative-infinity','NaN','explicit-tag-case','quoted-key','binary-key','nested','pairs','omap-binary-key','nonfinite-key','nonfinite-set','ordinary-date-scalars'])
def test_valid_YAML_scalar_metadata_preserves_originals_and_compiles(brain,monkeypatch,header):
    path,raw=original(header);assert w.meta(raw.decode('utf-8'))
    rows=w.inventory();calls=calls_for(monkeypatch,rows);report=w.run(1)
    assert report['status']=='complete' and len(calls)==1 and path.read_bytes()==raw

@pytest.mark.parametrize('number',[float('inf'),float('-inf'),float('nan')],ids=['positive-infinity','negative-infinity','NaN'])
def test_generic_JSON_and_decoded_claim_guards_remain_strict(brain,number):
    assert not math.isfinite(number)
    with pytest.raises(ValueError):w._bounded_json({'value':number},allow_nan=False)
    with pytest.raises(ValueError):w._screen_decoded_context({'value':number})

def measure_detector_work(monkeypatch):
    observed={'calls':0,'units':0};old_clean=s.clean_text
    def clean(value,*args,**kwargs):
        observed['calls']+=1;observed['units']+=len(value) if isinstance(value,str) else 0
        return old_clean(value,*args,**kwargs)
    class Pattern:
        def __init__(self,compiled):self.compiled=compiled
        def search(self,value,*args,**kwargs):
            observed['calls']+=1;observed['units']+=len(value)
            return self.compiled.search(value,*args,**kwargs)
    class Regex:
        def __getattr__(self,name):return getattr(re,name)
        def compile(self,pattern,flags=0):
            compiled=re.compile(pattern,flags)
            return Pattern(compiled) if pattern==s.SECRET.pattern.encode('ascii') else compiled
    monkeypatch.setattr(s,'clean_text',clean);monkeypatch.setattr(w,'re',Regex())
    return observed

def aliased_header(kind,shape,aliases,size=700000):
    scalar=binary(b'\xff'+b'A'*(size-1)) if kind=='binary' else '"'+'A'*size+'"'
    if shape=='mapping':return 'first: &same '+scalar+'\n'+''.join('ref'+str(i)+': *same\n' for i in range(aliases))
    return 'annotation: !!pairs [{first: &same '+scalar+'}'+''.join(', {ref'+str(i)+': *same}' for i in range(aliases))+']'

@pytest.mark.parametrize('kind',['string','binary'])
@pytest.mark.parametrize('shape',['mapping','pairs'])
@pytest.mark.parametrize('aliases',[0,1,20,100],ids=['fitting-original','fitting-one-alias','twenty-aliases','hundred-aliases'])
def test_scalar_association_work_obeys_text_budget_without_charging_pairs_twice(brain,monkeypatch,kind,shape,aliases):
    path,raw=original(aliased_header(kind,shape,aliases));assert w.meta(raw.decode('utf-8'))
    rows=w.inventory();observed=measure_detector_work(monkeypatch)
    if aliases>=20:
        with pytest.raises(w.WikiIntegrityError) as caught:w._validate_source_context({'sources':[rows[0]]})
        assert caught.value.page=='01-Projects/Example.md' and caught.value.field=='content'
    else:w._validate_source_context({'sources':[rows[0]]})
    assert path.read_bytes()==raw and observed['calls']>0
    assert observed['units']<=8_000_000, 'Detector scalar and association work must stop with the ordinary source text budget.'

@pytest.mark.parametrize('length,allowed',[(999994,True),(999995,False)],ids=['fitting-two-million','one-over-two-million'])
def test_existing_two_million_semantic_text_boundary_is_not_double_charged(brain,monkeypatch,length,allowed):
    path,raw=original('first: &same '+binary(b'A'*length)+'\nsecond: *same')
    decoded=w.meta(raw.decode('utf-8'));assert decoded['first'] is decoded['second'] and 2*length+len('firstsecond')==(1_999_999 if allowed else 2_000_001)
    rows=w.inventory();observed=measure_detector_work(monkeypatch)
    if allowed:w._validate_source_context({'sources':[rows[0]]})
    else:
        with pytest.raises(w.WikiIntegrityError):w._validate_source_context({'sources':[rows[0]]})
    assert path.read_bytes()==raw and observed['units']<=8_000_000

@pytest.mark.parametrize('kind',['string','binary'])
def test_sensitive_association_after_fitting_aliases_still_stops_before_provider(brain,monkeypatch,kind):
    header=escaped('password')+': '+binary('SyntheticAssociationValue1234567890')+'\n'+aliased_header(kind,'mapping',1)
    path,raw=original(header);rows=w.inventory();calls=calls_for(monkeypatch,rows);report=w.run(1)
    assert not calls and report['status']=='attention' and report['integrity_field']=='content'
    assert report['integrity_page']=='01-Projects/Example.md' and path.read_bytes()==raw

@pytest.mark.parametrize('header',[
    'outer: &same {label: ordinary, again: *same}',
    'outer: &same [!!pairs [{label: ordinary}], *same]',
],ids=['recursive-mapping','recursive-pair-sequence'])
def test_deferred_pair_frames_do_not_hide_actual_graph_cycles(brain,monkeypatch,header):
    path,raw=original(header);rows=w.inventory();calls=calls_for(monkeypatch,rows);report=w.run(1)
    assert not calls and report['status']=='attention' and report['integrity_field']=='content' and path.read_bytes()==raw

@pytest.mark.parametrize('boundary',['nodes','depth'])
def test_detector_pair_frames_are_not_additional_YAML_graph_nodes_or_depth(brain,boundary):
    if boundary=='nodes':
        value={'a':1,'b':2};w._screen_source_metadata(value,max_nodes=5)
        with pytest.raises(ValueError):w._screen_source_metadata(value,max_nodes=4)
    else:
        value={'annotation':[('label',b'ordinary')]};w._screen_source_metadata(value,max_depth=3)
        with pytest.raises(ValueError):w._screen_source_metadata(value,max_depth=2)

@pytest.mark.parametrize('kind',['string','binary'])
def test_fitting_shared_container_aliases_keep_the_existing_seen_graph_contract(brain,monkeypatch,kind):
    scalar=binary(b'A'*700000) if kind=='binary' else '"'+'A'*700000+'"'
    path,raw=original('first: &same ['+scalar+']\nsecond: *same\nthird: *same')
    decoded=w.meta(raw.decode('utf-8'));assert decoded['first'] is decoded['second'] is decoded['third']
    rows=w.inventory();observed=measure_detector_work(monkeypatch)
    w._validate_source_context({'sources':[rows[0]]})
    assert path.read_bytes()==raw and observed['units']<=8_000_000
