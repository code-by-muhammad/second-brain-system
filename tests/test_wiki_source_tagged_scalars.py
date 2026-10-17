"""Existing standard YAML spellings retain decoded evidence before lossy construction."""
import base64,json,re
import pytest,yaml
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_remediation import fake_model
from test_wiki_compact_aggregate_budget import QUOTE,skip_output

TOKEN='ghp_'+'SyntheticTaggedValueA12345'*2
NUMBER='1:0:0:0:0:0:0:0:0:0'
def escaped(value):return '"'+''.join('\\u'+format(ord(c),'04x') for c in value)+'"'
PASSWORD=escaped('password')
def encoded(value):return '"'+base64.b64encode(value if isinstance(value,bytes) else value.encode()).decode('ascii')+'"'
def mapped(tag,scalar):return '!!'+tag+' {=: '+scalar+'}'
def entry(name,expression,header,unsafe,*,prefix='',invalid=False):
    return {'id':name,'expression':expression,'header':header,'unsafe':unsafe,'prefix':prefix,'invalid':invalid}

CASES=[]
for tag,value in [('binary',encoded(TOKEN)),('int','"'+NUMBER+'"'),('str','"'+'A'*20+'"')]:
    expression=mapped(tag,value);key='annotation' if tag=='binary' else PASSWORD
    CASES.append(entry(tag+'-map-duplicate',expression,key+': '+expression+'\n'+key+': ordinary',True))
    CASES.append(entry(tag+'-map-constructor-fallback',expression,'reviewed: !!bool maybe\n'+key+': '+expression,True))
expression=mapped('binary',encoded(TOKEN))
CASES.append(entry('binary-wrapper-alias-discarded',expression,'value: &wrapper '+expression+'\nannotation: *wrapper\nvalue: ordinary\nannotation: ordinary',True))
expression=mapped('int','"'+NUMBER+'"')
CASES.append(entry('integer-wrapper-alias-outer-association',expression,'value: &wrapper '+expression+'\n'+PASSWORD+': *wrapper\n'+PASSWORD+': ordinary',True))
expression=mapped('int','{=: "'+NUMBER+'"}')
CASES.append(entry('integer-recursive-value-mapping',expression,PASSWORD+': '+expression+'\n'+PASSWORD+': ordinary',True))
prefix='encoded: &part '+encoded(TOKEN);expression=mapped('binary','*part')
CASES.append(entry('binary-aliased-value-scalar',expression,prefix+'\nannotation: '+expression+'\nannotation: ordinary\nencoded: ordinary',True,prefix=prefix))
expression='! '+NUMBER
CASES.append(entry('nonspecific-integer-duplicate',expression,PASSWORD+': '+expression+'\n'+PASSWORD+': ordinary',True))
CASES.append(entry('nonspecific-integer-constructor-fallback',expression,'reviewed: !!bool maybe\n'+PASSWORD+': '+expression,True))
CASES.append(entry('nonspecific-integer-alias-association',expression,'value: &same '+expression+'\n'+PASSWORD+': *same\n'+PASSWORD+': ordinary',True))
expression=mapped('binary',encoded(b'\xffordinary'))
CASES.append(entry('benign-opaque-binary-value-map',expression,'annotation: '+expression,False))
expression=mapped('binary','"A"')
CASES.append(entry('benign-invalid-binary-value-map-fallback',expression,'annotation: '+expression,False,invalid=True))
expression=mapped('int','"0x0000000000000001"')
CASES.append(entry('benign-mapped-integer-normalization',expression,PASSWORD+': '+expression,False))
expression=mapped('str','"short"')
CASES.append(entry('benign-string-value-map',expression,PASSWORD+': '+expression,False))
expression='! 1:30'
CASES.append(entry('benign-nonspecific-sexagesimal',expression,PASSWORD+': '+expression,False))
expression='! 0x0000000000000001'
CASES.append(entry('benign-nonspecific-padded-hex-normalization',expression,PASSWORD+': '+expression,False))
expression='! "ordinary"'
CASES.append(entry('benign-nonspecific-quoted-string',expression,'annotation: '+expression,False))
expression='!!int {different: ordinary}'
CASES.append(entry('benign-invalid-value-map-fallback',expression,'annotation: '+expression,False,invalid=True))

# Additional valid constructor shapes, first-selector rules and safe fallbacks.
for tag,first,last in [('binary',encoded(b'ordinary'),encoded(TOKEN)),('int','"1"','"'+NUMBER+'"'),('str','"short"','"'+'A'*20+'"')]:
    expression='!!'+tag+' {=: '+first+', =: '+last+'}'
    CASES.append(entry('first-selector-benign-'+tag,expression,('annotation' if tag=='binary' else PASSWORD)+': '+expression,False))
    expression='!!'+tag+' {=: '+last+', =: '+first+'}'
    key='annotation' if tag=='binary' else PASSWORD
    CASES.append(entry('first-selector-unsafe-'+tag,expression,key+': '+expression+'\n'+key+': ordinary',True))
for tag,scalar in [('binary',encoded(TOKEN)),('int','"'+NUMBER+'"'),('str','"'+'A'*20+'"')]:
    expression='!!'+tag+' {"=": '+scalar+'}'
    CASES.append(entry('quoted-equals-is-not-value-tag-'+tag,expression,'annotation: '+expression,False,invalid=True))
expression=mapped('binary',mapped('str',encoded(TOKEN)))
CASES.append(entry('binary-recursive-tagged-value',expression,'annotation: '+expression+'\nannotation: ordinary',True))
expression=mapped('int','!!str "'+NUMBER+'"')
CASES.append(entry('integer-selected-inner-string-tag',expression,PASSWORD+': '+expression+'\n'+PASSWORD+': ordinary',True))
expression=mapped('str','!!float 12345678901234567890')
CASES.append(entry('string-selected-inner-float-raw-spelling',expression,PASSWORD+': '+expression+'\n'+PASSWORD+': ordinary',True))
expression=mapped('str','!!int 1:30')
CASES.append(entry('benign-string-selected-inner-int-raw-spelling',expression,PASSWORD+': '+expression,False))
expression=mapped('int','!!bool 1:30')
CASES.append(entry('benign-outer-int-ignores-invalid-inner-tag',expression,PASSWORD+': '+expression,False))
prefix='part: &part {=: '+encoded(TOKEN)+'}';expression=mapped('binary','*part')
CASES.append(entry('binary-selected-ordinary-mapping-alias',expression,prefix+'\nannotation: '+expression+'\nannotation: ordinary',True,prefix=prefix))
prefix='part: &part {=: "'+NUMBER+'"}';expression=mapped('int','*part')
CASES.append(entry('integer-selected-ordinary-mapping-alias',expression,prefix+'\n'+PASSWORD+': '+expression+'\n'+PASSWORD+': ordinary',True,prefix=prefix))
for tag,scalar in [('null','A'*20),('bool','true'),('float','.inf'),('float','.nan'),('float','12345678901234567890')]:
    expression=mapped(tag,scalar)
    CASES.append(entry('benign-standard-mapped-'+tag+'-'+scalar[:4],expression,PASSWORD+': '+expression,False))
expression='!!timestamp {=: "2026-10-16T12:34:56Z"}'
CASES.append(entry('benign-mapped-timestamp-remains-invalid-fallback',expression,'annotation: '+expression,False,invalid=True))
expression='!!int {=: [ordinary, safe]}'
CASES.append(entry('benign-sequence-selector-remains-invalid-fallback',expression,'annotation: '+expression,False,invalid=True))
expression='! "1:30"'
CASES.append(entry('benign-nonspecific-quoted-numeric-is-string',expression,PASSWORD+': '+expression,False))
expression=mapped('int','{=: {=: "1:30"}}')
CASES.append(entry('benign-recursive-ordinary-value-mappings',expression,PASSWORD+': '+expression,False))
expression='!!binary {!!value "chosen": '+encoded(TOKEN)+'}'
CASES.append(entry('explicit-value-tag-key-selects-binary',expression,'annotation: '+expression+'\nannotation: ordinary',True))

def recover(case,text=None):
    expression=case['expression'];prefix=case['prefix']
    if text is not None:
        start=text.index(expression);expression=text[start:start+len(expression)]
        if prefix:
            start=text.index(prefix);prefix=text[start:start+len(prefix)]
    return yaml.safe_load((prefix+'\n' if prefix else '')+'value: '+expression)['value']

def known_signal(value):
    if isinstance(value,bytes):return bool(re.compile(s.SECRET.pattern.encode('ascii'),s.SECRET.flags & ~re.UNICODE).search(value))
    text=value if isinstance(value,str) else json.dumps(value,allow_nan=True)
    return bool(s.SECRET.search(text) or s.SECRET.search('password: '+text))

@pytest.mark.parametrize('case',CASES,ids=[case['id'] for case in CASES])
def test_standard_scalar_spellings_preserve_lossless_source_preflight(brain,monkeypatch,tmp_path,case):
    handled=False;decoded=None
    try:decoded=recover(case)
    except (yaml.YAMLError,ValueError,TypeError,KeyError,AttributeError,IndexError):
        assert case['invalid'];handled=True
    assert handled==case['invalid']
    signal=False if handled else known_signal(decoded)
    assert signal==case['unsafe']
    header=case['header'];raw=('---\n'+header+'\n---\n# Example Project\n'+QUOTE+'\n').encode('utf-8')
    assert len(raw)<3000 and not s.SECRET.search(raw.decode('utf-8'))
    path=s.VAULT/'01-Projects/Example.md';path.parent.mkdir();path.write_bytes(raw)
    metadata=w.meta(raw.decode('utf-8'));rows=w.inventory();assert len(rows)==1
    output=skip_output();calls=fake_model(monkeypatch,output)
    report=w.run(1);sent=None;sent_recovered=False
    if calls.inputs:
        packet=json.loads(calls.inputs[0][len(w.PROMPT):]);sent=packet['sources'][0]['text']
        if not handled:sent_recovered=known_signal(recover(case,sent))
    observation={'case':case['id'],'expected_unsafe':case['unsafe'],'known_standard_constructor_type':None if handled else type(decoded).__name__,'known_constructor_has_established_secret_signal':signal,'safe_invalid_constructor_fallback':handled,'raw_header_detector_matches':False,'source_bytes':len(raw),'original_sha256':s.digest(raw),'metadata_empty_after_loss_or_fallback':metadata=={},'actual_worker_status':report['status'],'actual_integrity_field':report.get('integrity_field'),'fake_provider_calls':len(calls),'complete_original_header_reached_fake_provider':sent is not None and header in sent,'same_known_value_recovered_from_sent_form':sent_recovered,'original_unchanged':path.read_bytes()==raw,'pending_source_parts_after':w.status()['pending_source_parts'],'actual_external_provider_calls':0,'production_notes_used':False}
    (tmp_path/'observation.json').write_text(json.dumps(observation,indent=2)+'\n',encoding='utf-8')
    assert observation['original_unchanged']
    if case['unsafe']:
        assert report['status']=='attention' and report.get('integrity_field')=='content' and not calls
        assert w.inventory()==rows and w.status()['pending_source_parts']==len(rows)
        assert not list((cfg.STATE/'wiki/batches').glob('*/input.json'))
    else:assert report['status']=='complete' and len(calls)==1

from test_wiki_source_yaml_events import original,provider,assert_run

@pytest.mark.parametrize('direction',['Unicode-key-binary-value','binary-key-Unicode-value','mapped-string-key','mapped-binary-key'])
@pytest.mark.parametrize('unsafe',[True,False],ids=['unsafe','benign'])
def test_mapped_scalar_keys_and_mixed_views_keep_sensitive_associations(brain,monkeypatch,direction,unsafe):
    value='A'*20 if unsafe else 'rotate safely'
    if direction=='Unicode-key-binary-value':key=escaped('pa\u017f\u017fword');scalar=mapped('binary',encoded(value))
    elif direction=='binary-key-Unicode-value':key=mapped('binary',encoded(b'password'));scalar=mapped('str',escaped('\u00a0'+value))
    elif direction=='mapped-string-key':key=mapped('str',PASSWORD);scalar=escaped(value)
    else:key=mapped('binary',encoded(b'password'));scalar=escaped(value)
    header=key+': '+scalar+'\n'+key+': '+escaped('rotate safely')
    path,raw,rows=original(header);calls=provider(monkeypatch,rows)
    assert_run(path,raw,rows,calls,unsafe)

@pytest.mark.parametrize('kind',['binary-map','integer-map','nonspecific-integer'])
@pytest.mark.parametrize('mode',['unused-packet','outside-first-chunk'])
def test_complete_source_and_unused_resume_recheck_mapped_scalar_evidence(brain,monkeypatch,kind,mode):
    key='annotation' if kind=='binary-map' else PASSWORD
    expression=mapped('binary',encoded(TOKEN)) if kind=='binary-map' else mapped('int','"'+NUMBER+'"') if kind=='integer-map' else '! '+NUMBER
    header=key+': '+expression+'\n'+key+': ordinary'
    if mode=='outside-first-chunk':header='padding: "'+'A'*30000+'"\n'+header
    path,raw,rows=original(header);saved=None
    if mode=='unused-packet':
        with monkeypatch.context() as legacy:
            legacy.setattr(w,'_validate_source_context',lambda packet:None);packet=w.prepare(1)
        folder=cfg.STATE/'wiki/batches'/packet['batch_id'];saved=(folder/'input.json').read_bytes()
    calls=provider(monkeypatch,rows)
    if saved is None:assert_run(path,raw,rows,calls,True)
    else:
        report=w.run(1)
        assert report['status']=='attention' and report['integrity_field']=='content' and not calls
        assert report['integrity_page']=='01-Projects/Example.md' and report['consecutive_failures']==0
        assert (folder/'input.json').read_bytes()==saved and path.read_bytes()==raw and w.inventory()==rows
        assert w.status()['pending_source_parts']==len(rows)

def measured_header(monkeypatch):
    """Forward actual detectors/constructors; observations do not replace them."""
    import sys
    observed={'active':False,'detectors':0,'renders':0,'conversion':0,'resolver':0,'binary_views':0,'binary_constructors':0,'numeric_constructors':0}
    actual_pattern=s.SECRET;actual_json=w.json;actual_re=re
    def charge(field,amount):
        if observed['active']:
            observed[field]+=amount
            assert sum(observed[k] for k in ('detectors','renders','conversion','resolver','binary_views'))<=8_000_000
    class Pattern:
        pattern=actual_pattern.pattern;flags=actual_pattern.flags
        def search(self,value,*args,**kwargs):charge('detectors',len(value));return actual_pattern.search(value,*args,**kwargs)
    class BytesPattern:
        def __init__(self,actual):self.actual=actual
        def search(self,value,*args,**kwargs):charge('detectors',len(value));return self.actual.search(value,*args,**kwargs)
    class Regex:
        def __getattr__(self,name):return getattr(actual_re,name)
        def compile(self,value,flags=0):return BytesPattern(actual_re.compile(value,flags))
    class JSON:
        def __getattr__(self,name):return getattr(actual_json,name)
        def dumps(self,value,*args,**kwargs):
            result=actual_json.dumps(value,*args,**kwargs)
            if type(value) in (int,float,bool) or value is None:charge('renders',len(result))
            return result
    monkeypatch.setattr(s,'SECRET',Pattern());monkeypatch.setattr(w,'re',Regex());monkeypatch.setattr(w,'json',JSON())
    resolve=yaml.SafeLoader.resolve
    def resolving(self,kind,value,implicit):
        if kind is yaml.nodes.ScalarNode:charge('resolver',len(value))
        return resolve(self,kind,value,implicit)
    monkeypatch.setattr(yaml.SafeLoader,'resolve',resolving)
    for name in ('binary','str','int','float','bool','null','timestamp'):
        actual=getattr(yaml.SafeLoader,'construct_yaml_'+name)
        def make_constructor(actual,name):
            def construct(self,node):
                if observed['active']:
                    raw_node=node
                    while isinstance(raw_node,yaml.nodes.MappingNode):
                        selected=next((v for k,v in raw_node.value if k.tag=='tag:yaml.org,2002:value'),None)
                        if selected is None:break
                        raw_node=selected
                    raw=raw_node.value if isinstance(raw_node,yaml.nodes.ScalarNode) else ''
                    if name not in ('binary','str'):charge('conversion',len(raw)**2 if name=='int' and ':' in raw else len(raw))
                    if name=='binary':observed['binary_constructors']+=1
                    if name in ('int','float','bool','null'):observed['numeric_constructors']+=1
                result=actual(self,node)
                if name=='binary':charge('renders',len(result))
                return result
            return construct
        monkeypatch.setattr(yaml.SafeLoader,'construct_yaml_'+name,make_constructor(actual,name))
    prior=sys.getprofile()
    def profile(frame,event,arg):
        if event=='c_call' and frame.f_code.co_name=='binary_view' and getattr(arg,'__name__',None)=='decode' and isinstance(getattr(arg,'__self__',None),bytes):charge('binary_views',len(arg.__self__))
    def check(header,*,attention=False):
        observed['active']=True;sys.setprofile(profile)
        try:
            if attention:
                with pytest.raises(ValueError):w._screen_source_header('---\n'+header+'\n---\n')
            else:assert w._screen_source_header('---\n'+header+'\n---\n')<=8_000_000
        finally:sys.setprofile(prior);observed['active']=False
        assert sum(observed[k] for k in ('detectors','renders','conversion','resolver','binary_views'))<=8_000_000
        return observed
    return check

@pytest.mark.parametrize('kind',['mapped-numeric-thousand','mapped-numeric-overflow','mapped-binary-thousand','selected-scalar-cache','nested-wrapper-cache','mapped-text-semantic-fitting','mapped-text-semantic-overflow','mapped-opaque-fitting'])
def test_value_mapping_aliases_share_one_bounded_normalization_and_event_ledger(brain,monkeypatch,tmp_path,kind):
    if kind.startswith('mapped-numeric'):
        aliases=1000 if kind.endswith('thousand') else 10000
        header='first: &same '+mapped('int','"'+'1'*4000+'"')+'\n'+''.join('ref'+str(i)+': *same\n' for i in range(aliases))
    elif kind=='mapped-binary-thousand':header='first: &same '+mapped('binary',encoded(b'ordinary'))+'\n'+''.join('ref'+str(i)+': *same\n' for i in range(1000))
    elif kind=='selected-scalar-cache':header='raw: &part '+encoded(b'ordinary')+'\n'+''.join('ref'+str(i)+': '+mapped('binary','*part')+'\n' for i in range(1000))
    elif kind=='nested-wrapper-cache':header='raw: &part {=: '+encoded(b'ordinary')+'}\n'+''.join('ref'+str(i)+': '+mapped('binary','*part')+'\n' for i in range(1000))
    elif kind.startswith('mapped-text-semantic'):
        size=999980 if kind.endswith('fitting') else 1000010
        header='first: &same '+mapped('str','"'+'A'*size+'"')+'\nsecond: *same'
    else:header='ordinary: '+mapped('binary',encoded(b'\xff'+b'A'*799999))
    assert len(header.encode('utf-8'))<2_000_000
    observed=measured_header(monkeypatch)(header,attention=kind.endswith('overflow'))
    if kind in ('mapped-binary-thousand','selected-scalar-cache','nested-wrapper-cache'):assert observed['binary_constructors']==1
    if kind=='mapped-numeric-thousand':assert observed['numeric_constructors']==1
    (tmp_path/'bounded-event-work.json').write_text(json.dumps(observed,indent=2)+'\n',encoding='utf-8')

@pytest.mark.parametrize('kind',['active-selected-cycle','selected-sequence-cycle','aliased-mapping-depth','selected-map-node-budget','benign-default-map-alias','benign-default-sequence-alias'])
def test_raw_value_descriptors_retain_collection_alias_and_structural_guards(brain,kind):
    if kind=='active-selected-cycle':header='first: &same !!int {=: *same}'
    elif kind=='selected-sequence-cycle':header='first: &same !!str {=: [*same]}'
    elif kind=='aliased-mapping-depth':header='first: !!str '+'{=: '*130+'ordinary'+'}'*130
    elif kind=='selected-map-node-budget':
        header='first: !!str {=: ordinary}'
        with pytest.raises(ValueError):w._screen_source_header('---\n'+header+'\n---\n',max_nodes=4)
        assert w._screen_source_header('---\n'+header+'\n---\n',max_nodes=5)<=8_000_000
        return
    elif kind=='benign-default-map-alias':header='first: &same {ordinary: safe}\nsecond: *same'
    else:header='first: &same [ordinary, safe]\nsecond: *same'
    if kind.startswith('benign'):assert w._screen_source_header('---\n'+header+'\n---\n')<=8_000_000
    else:
        with pytest.raises(ValueError):w._screen_source_header('---\n'+header+'\n---\n')

@pytest.mark.parametrize('tag',['int','binary','float','bool'])
def test_invalid_inner_scalar_cache_keeps_mapped_constructor_fallback_shape(brain,monkeypatch,tag):
    # The inner scalar fails first and caches a lexical fallback. The outer
    # standard constructor fails on the same raw value but must stay a
    # collection, rather than invent a password association from that cache.
    value='A'*(21 if tag=='binary' else 20)
    expression=mapped(tag,'!!'+tag+' "'+value+'"')
    header=PASSWORD+': '+expression
    path,raw,rows=original(header)
    assert w.meta(raw.decode('utf-8'))=={}
    calls=provider(monkeypatch,rows);assert_run(path,raw,rows,calls,False)
