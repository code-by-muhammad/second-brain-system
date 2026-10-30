"""Complete known YAML evidence before constructors discard it, with bounded work."""
import base64,json,re,sys
import pytest,yaml
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_remediation import fake_model
from test_wiki_compact_aggregate_budget import QUOTE,skip_output

TOKEN='ghp_'+'SyntheticLostValueA12345'*2
VALUE='A'*20
def escaped(value):return '"'+''.join('\\u'+format(ord(c),'04x') for c in value)+'"'
def binary(value):return '!!binary "'+base64.b64encode(value if isinstance(value,bytes) else value.encode()).decode('ascii')+'"'
def pair(key,value):return (binary(key) if isinstance(key,bytes) else escaped(key))+': '+(binary(value) if isinstance(value,bytes) else escaped(value))
def original(header,*,opening=True,closed=True,newline='\n',bom=False):
    path=s.VAULT/'01-Projects/Example.md';path.parent.mkdir(parents=True)
    text=('---\n' if opening else '')+header+('\n---\n' if closed and opening else '\n')+'# Example Project\n'+QUOTE+'\n'
    raw=(('\ufeff' if bom else '')+text.replace('\n',newline)).encode('utf-8')
    assert len(raw)<2_000_000 and not s.SECRET.search(raw.decode('utf-8-sig'))
    path.write_bytes(raw);rows=w.inventory();assert rows
    return path,raw,rows
def provider(monkeypatch,rows):
    output=skip_output();text=rows[0]['text'];output['dispositions'][0]['quote']=QUOTE if QUOTE in text else text[-40:].strip()
    return fake_model(monkeypatch,output)
def assert_run(path,raw,rows,calls,unsafe):
    report=w.run(1);assert path.read_bytes()==raw
    if unsafe:
        assert report['status']=='attention' and report['integrity_field']=='content' and not calls
        assert report['integrity_page']=='01-Projects/Example.md' and report['consecutive_failures']==0
        assert w.inventory()==rows and w.status()['pending_source_parts']==len(rows)
        assert not list((cfg.STATE/'wiki/batches').glob('*/input.json'))
        assert TOKEN not in json.dumps({'report':report,'worker':w.status()['worker']})
    else:assert report['status']=='complete' and len(calls)==1

FORMS=['duplicate-key','merge-override','set-value','explicit-null','invalid-bool','deep-fallback','discarded-binary','duplicate-association','merge-association']
def discarded(form,sensitive):
    scalar=escaped(TOKEN if sensitive else 'Ordinary owner annotation')
    if form=='duplicate-key':return 'annotation: '+scalar+'\nannotation: ordinary'
    if form=='merge-override':return 'annotation: {<<: {note: '+scalar+'}, note: ordinary}'
    if form=='set-value':return 'annotation: !!set {ordinary: '+scalar+'}'
    if form=='explicit-null':return 'annotation: !!null '+scalar
    if form=='invalid-bool':return 'reviewed: !!bool maybe\nannotation: '+scalar
    if form=='deep-fallback':return 'padding: '+'['*1000+'ordinary'+']'*1000+'\nannotation: '+scalar
    if form=='discarded-binary':return 'annotation: '+binary(TOKEN if sensitive else 'Ordinary owner annotation')+'\nannotation: ordinary'
    first=pair('password',VALUE if sensitive else 'rotate safely');last=pair('password','rotate safely')
    return first+'\n'+last if form=='duplicate-association' else 'annotation: {<<: {'+first+'}, '+last+'}'

@pytest.mark.parametrize('form',FORMS)
@pytest.mark.parametrize('sensitive',[True,False],ids=['unsafe','benign-or-documented-depth-policy'])
def test_known_discarded_scalar_and_pair_evidence_is_screened_before_model(brain,monkeypatch,form,sensitive):
    path,raw,rows=original(discarded(form,sensitive));calls=provider(monkeypatch,rows)
    # A 1000-deep benign header deliberately changes from lenient {} fallback
    # to depth128 attention; it is a new policy case, not the old root control.
    assert_run(path,raw,rows,calls,sensitive or form=='deep-fallback')

@pytest.mark.parametrize('kind',['scalar-alias','key-alias','binary-alias','mixed-Unicode-key','mixed-Unicode-value','typed-pairs'])
@pytest.mark.parametrize('sensitive',[True,False],ids=['unsafe','benign'])
def test_discarded_scalar_aliases_and_typed_mixed_pairs_keep_every_association(brain,monkeypatch,kind,sensitive):
    value=VALUE if sensitive else 'rotate safely';key=escaped('password')
    if kind=='scalar-alias':header='value: &same '+escaped(value)+'\n'+key+': *same\n'+key+': "rotate safely"'
    elif kind=='key-alias':header='? &key '+key+'\n: '+escaped(value)+'\n? *key\n: "rotate safely"'
    elif kind=='binary-alias':header='value: &same '+binary(value)+'\n'+key+': *same\n'+key+': "rotate safely"'
    elif kind=='mixed-Unicode-key':header=pair('pa\u017f\u017fword',value.encode())+'\n'+pair('pa\u017f\u017fword','rotate safely')
    elif kind=='mixed-Unicode-value':header=pair(b'password','\u00a0'+value)+'\n'+pair(b'password','rotate safely')
    else:header='annotation: !!pairs [{'+pair('password',value.encode())+'}, {'+pair('password','rotate safely')+'}]\nannotation: ordinary'
    path,raw,rows=original(header);calls=provider(monkeypatch,rows);assert_run(path,raw,rows,calls,sensitive)

@pytest.mark.parametrize('tag,value,unsafe',[
    ('int','1:0:0:0:0:0:0:0:0:0',True),
    ('int','0x0000000000000001',False),
    ('float','12345678901234567890',False),
    ('null',VALUE,False),
    ('bool','true',False),
    ('timestamp','2026-10-15T12:34:56Z',False),
    ('binary','"A"',False),
    ('int','1:30',False),
],ids=['discarded-sexagesimal-credential','benign-padded-hex','benign-scientific-normalization','benign-typed-null','benign-typed-bool','benign-typed-timestamp','benign-invalid-binary-fallback','ordinary-sexagesimal'])
def test_known_typed_pairs_use_bounded_normalized_views_not_raw_spelling(brain,monkeypatch,tag,value,unsafe):
    first=escaped('password')+': !!'+tag+' '+value
    header=first+'\n'+escaped('password')+': "rotate safely"' if unsafe else first
    path,raw,rows=original(header);calls=provider(monkeypatch,rows);assert_run(path,raw,rows,calls,unsafe)

@pytest.mark.parametrize('kind,unsafe',[
    ('ordinary-Markdown',False),('unclosed-opening',False),('nonmapping-sequence-safe',False),('nonmapping-sequence-unsafe',True),
    ('nonmapping-scalar-unsafe',True),('closed-syntax-error',True),('unknown-safe-tag',False),('undefined-alias',True),('ambiguous-anchor',True),('collection-cycle',True),
])
def test_exact_header_recognition_and_complete_parse_policy_are_explicit(brain,monkeypatch,kind,unsafe):
    headers={'ordinary-Markdown':escaped(TOKEN),'unclosed-opening':'annotation: '+escaped(TOKEN),
        'nonmapping-sequence-safe':'- ordinary\n- safe','nonmapping-sequence-unsafe':'- '+escaped(TOKEN),
        'nonmapping-scalar-unsafe':escaped(TOKEN),'closed-syntax-error':'annotation: [broken',
        'unknown-safe-tag':'annotation: !owner-custom ordinary','undefined-alias':'annotation: *missing',
        'ambiguous-anchor':'first: &same ordinary\nsecond: &same safe\nthird: *same','collection-cycle':'outer: &same [ordinary, *same]'}
    path,raw,rows=original(headers[kind],opening=kind!='ordinary-Markdown',closed=kind!='unclosed-opening')
    calls=provider(monkeypatch,rows);assert_run(path,raw,rows,calls,unsafe)

@pytest.mark.parametrize('newline,bom',[('\n',False),('\r\n',False),('\r',False),('\r\n',True)],ids=['LF','CRLF','CR','BOM-CRLF'])
def test_source_header_uses_existing_exact_fences_quoted_and_indented_evidence(brain,monkeypatch,newline,bom):
    header='annotation: |\n  ordinary --- inline\n  ---\nword: "Before --- after"\ndate: 2026-10-15'
    path,raw,rows=original(header,newline=newline,bom=bom);calls=provider(monkeypatch,rows);assert_run(path,raw,rows,calls,False)

@pytest.mark.parametrize('kind',['discarded-binary','invalid-bool'])
@pytest.mark.parametrize('mode',['unused-packet','outside-first-chunk'])
def test_event_evidence_is_rechecked_in_saved_packets_and_complete_sources(brain,monkeypatch,kind,mode):
    header=discarded(kind,True)
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

def measure_phases(monkeypatch):
    state={'phase':None,'event':{'detectors':0,'views':0,'resolver':0,'conversion_cost_bound':0,'constructors':0},'constructed':{'detectors':0,'views':0,'resolver':0,'conversion_cost_bound':0,'constructors':0}}
    actual_pattern=s.SECRET;actual_json=w.json;actual_re=re
    def charge(field,amount):
        phase=state['phase']
        if phase:
            state[phase][field]+=amount
            assert sum(state[phase][k] for k in ('detectors','views','resolver','conversion_cost_bound'))<=8_000_000
    class Pattern:
        pattern=actual_pattern.pattern;flags=actual_pattern.flags
        def search(self,value,*args,**kwargs):charge('detectors',len(value));return actual_pattern.search(value,*args,**kwargs)
    class BytesPattern:
        def __init__(self,value):self.value=value
        def search(self,value,*args,**kwargs):charge('detectors',len(value));return self.value.search(value,*args,**kwargs)
    class Regex:
        def __getattr__(self,name):return getattr(actual_re,name)
        def compile(self,value,flags=0):return BytesPattern(actual_re.compile(value,flags))
    class JSON:
        def __getattr__(self,name):return getattr(actual_json,name)
        def dumps(self,value,*args,**kwargs):
            result=actual_json.dumps(value,*args,**kwargs)
            if type(value) in (int,float,bool) or value is None:charge('views',len(result))
            return result
    def wrap_phase(actual,name):
        def call(*args,**kwargs):
            prior=state['phase'];state['phase']=name
            try:return actual(*args,**kwargs)
            finally:state['phase']=prior
        return call
    monkeypatch.setattr(s,'SECRET',Pattern());monkeypatch.setattr(w,'re',Regex());monkeypatch.setattr(w,'json',JSON())
    if hasattr(w,'_screen_source_header'):monkeypatch.setattr(w,'_screen_source_header',wrap_phase(w._screen_source_header,'event'))
    monkeypatch.setattr(w,'_screen_source_metadata',wrap_phase(w._screen_source_metadata,'constructed'))
    resolve=yaml.SafeLoader.resolve
    def resolving(self,kind,value,implicit):
        if state['phase']=='event':charge('resolver',len(value))
        return resolve(self,kind,value,implicit)
    monkeypatch.setattr(yaml.SafeLoader,'resolve',resolving)
    def wrap_constructor(actual,name):
        def construct(self,node):
            if state['phase']=='event':
                state['event']['constructors']+=1
                if name!='binary':charge('conversion_cost_bound',len(node.value)**2 if name=='int' and ':' in node.value else len(node.value))
            result=actual(self,node)
            if name=='binary' and state['phase']=='event':charge('views',len(result))
            return result
        return construct
    for name in ('binary','int','float','bool','null','timestamp'):
        method='construct_yaml_'+name;monkeypatch.setattr(yaml.SafeLoader,method,wrap_constructor(getattr(yaml.SafeLoader,method),name))
    old_profile=sys.getprofile()
    def profile(frame,event,arg):
        if event=='c_call' and frame.f_code.co_name=='binary_view' and getattr(arg,'__name__',None)=='decode' and isinstance(getattr(arg,'__self__',None),bytes):charge('views',len(arg.__self__))
    return state,profile,old_profile

@pytest.mark.parametrize('kind',['numeric-thousand','numeric-overflow','opaque-fitting','opaque-alias-fitting','opaque-alias-overflow','Unicode-alias-fitting','sexagesimal-conversion-overflow','closed-overlong-key-policy'])
def test_real_forwarded_event_and_constructed_work_share_explicit_finite_phase_envelopes(brain,monkeypatch,kind):
    if kind.startswith('numeric'):
        aliases=1000 if kind=='numeric-thousand' else 10000
        header='first: &same '+'1'*4000+'\n'+''.join('ref'+str(i)+': *same\n' for i in range(aliases))
    elif kind in ('opaque-fitting','closed-overlong-key-policy'):header=escaped('\u6f22'*(150 if kind=='opaque-fitting' else 200))+': '+binary(b'\xff'+b'A'*1498999)
    elif kind.startswith('opaque-alias'):
        aliases=1 if kind=='opaque-alias-fitting' else 100
        header='first: &same '+binary(b'A'*999994)+'\n'+''.join('ref'+str(i)+': *same\n' for i in range(aliases))
    elif kind=='Unicode-alias-fitting':header=binary(b'first')+': &same "'+'\u6f22'*666600+'"\n'+binary(b'other')+': *same\n'+binary(b'third')+': *same'
    else:header='number: !!int '+':'.join('1' for _ in range(2000))
    path,raw,rows=original(header);state,profile,old_profile=measure_phases(monkeypatch)
    overflow=kind.endswith('overflow') or kind=='closed-overlong-key-policy'
    sys.setprofile(profile)
    try:
        if overflow:
            with pytest.raises(w.WikiIntegrityError) as caught:w._validate_source_context({'sources':[rows[0]]})
            assert caught.value.page=='01-Projects/Example.md' and caught.value.field=='content'
        else:w._validate_source_context({'sources':[rows[0]]})
    finally:sys.setprofile(old_profile)
    assert path.read_bytes()==raw
    totals={phase:sum(state[phase][k] for k in ('detectors','views','resolver','conversion_cost_bound')) for phase in ('event','constructed')}
    assert max(totals.values())<=8_000_000 and sum(totals.values())<=16_000_000
    if kind=='numeric-thousand' and hasattr(w,'_screen_source_header'):assert state['event']['constructors']==1
