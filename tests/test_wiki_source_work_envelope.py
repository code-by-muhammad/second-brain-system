"""Bound real source detector/render work without changing its semantic text budget."""
import base64,json,math,re
import pytest
import brain_store as s
import brain_wiki as w

MAX_WORK=8_000_000
QUOTE='A synthetic historical observation remains available.'
def binary(value):
    raw=value.encode('utf-8') if isinstance(value,str) else value
    return '!!binary "'+base64.b64encode(raw).decode('ascii')+'"'
def escaped(value):return '"'+''.join('\\u'+format(ord(c),'04x') for c in value)+'"'
def original(header):
    path=s.VAULT/'01-Projects/Example.md';path.parent.mkdir(parents=True)
    raw=('---\n'+header+'\n---\n# Example Project\n'+QUOTE+'\n').encode('utf-8');assert len(raw)<2_000_000 and not s.SECRET.search(raw.decode('utf-8'));path.write_bytes(raw)
    rows=w.inventory();assert rows
    return path,raw,rows

def measure_source_work(monkeypatch):
    observed={'active':False,'detector_units':0,'render_units':0,'renders':0};actual_pattern=s.SECRET;actual_json=w.json;actual_screen=w._screen_source_metadata
    class Pattern:
        pattern=actual_pattern.pattern;flags=actual_pattern.flags
        def search(self,value,*args,**kwargs):
            if observed['active']:observed['detector_units']+=len(value)
            return actual_pattern.search(value,*args,**kwargs)
    class BinaryPattern:
        def __init__(self,compiled):self.compiled=compiled
        def search(self,value,*args,**kwargs):
            if observed['active']:observed['detector_units']+=len(value)
            return self.compiled.search(value,*args,**kwargs)
    class Regex:
        def __getattr__(self,name):return getattr(re,name)
        def compile(self,pattern,flags=0):
            compiled=re.compile(pattern,flags)
            return BinaryPattern(compiled) if pattern==actual_pattern.pattern.encode('ascii') else compiled
    class JSON:
        def __getattr__(self,name):return getattr(actual_json,name)
        def dumps(self,value,*args,**kwargs):
            result=actual_json.dumps(value,*args,**kwargs)
            if observed['active'] and (type(value) in (int,float,bool) or value is None):observed['render_units']+=len(result);observed['renders']+=1
            return result
    def screen(*args,**kwargs):
        observed['active']=True
        try:return actual_screen(*args,**kwargs)
        finally:observed['active']=False
    monkeypatch.setattr(s,'SECRET',Pattern());monkeypatch.setattr(w,'re',Regex());monkeypatch.setattr(w,'json',JSON());monkeypatch.setattr(w,'_screen_source_metadata',screen)
    return observed
def preflight(rows,observed,*,allow_attention=False):
    outcome='accepted'
    try:w._validate_source_context({'sources':[rows[0]]})
    except w.WikiIntegrityError as error:
        assert allow_attention and error.page=='01-Projects/Example.md' and error.field=='content'
        outcome='attention'
    assert observed['detector_units']+observed['render_units']<=MAX_WORK
    return outcome

def numeric_aliases(keykind,shape,aliases,scalar=None):
    scalar=scalar or ('1'*4000 if keykind=='string' else '-'+'1'*4000)
    key=lambda value: value if keykind=='string' else binary(value)
    first=key('first')+': &same '+scalar
    if shape=='mapping':return first+'\n'+''.join(key('ref'+str(i))+': *same\n' for i in range(aliases))
    return 'annotation: !!pairs [{'+first+'}'+''.join(', {'+key('ref'+str(i))+': *same}' for i in range(aliases))+']'

@pytest.mark.parametrize('keykind',['string','binary'])
@pytest.mark.parametrize('shape',['mapping','pairs'])
@pytest.mark.parametrize('aliases',[0,1,1000,3000,10000],ids=['original','one-alias','fitting-cached-thousand','overflow-three-thousand','overflow-ten-thousand'])
def test_large_numeric_aliases_have_one_source_work_envelope(brain,monkeypatch,keykind,shape,aliases):
    path,raw,rows=original(numeric_aliases(keykind,shape,aliases));decoded=w.meta(raw.decode('utf-8'))
    values=list(decoded.values()) if shape=='mapping' else [pair[1] for pair in decoded['annotation']]
    assert len(values)==aliases+1 and all(type(value) is int and value is values[0] for value in values)
    observed=measure_source_work(monkeypatch);preflight(rows,observed,allow_attention=aliases>=3000)
    assert path.read_bytes()==raw

@pytest.mark.parametrize('scalar',['1.25','-.inf','.nan','true','null'],ids=['finite-float','negative-infinity','NaN','boolean','null'])
def test_ordinary_typed_aliases_stay_supported_with_bounded_actual_work(brain,monkeypatch,scalar):
    path,raw,rows=original(numeric_aliases('string','mapping',3000,scalar));observed=measure_source_work(monkeypatch)
    assert preflight(rows,observed)=='accepted' and path.read_bytes()==raw

def test_distinct_normal_integers_do_not_require_alias_identity_to_compile(brain,monkeypatch):
    header='\n'.join('n'+str(i)+': '+str(10**99+i) for i in range(3000))
    path,raw,rows=original(header);values=list(w.meta(raw.decode('utf-8')).values());assert len(set(map(id,values)))==3000
    observed=measure_source_work(monkeypatch);assert preflight(rows,observed)=='accepted' and path.read_bytes()==raw

@pytest.mark.parametrize('kind',['ASCII-one-pair','opaque-semantic-boundary','Unicode-binary-key','aliased-Unicode-binary-keys','Unicode-key-opaque-binary'])
def test_fitting_text_binary_and_UTF8_associations_keep_their_existing_semantics(brain,monkeypatch,kind):
    if kind=='ASCII-one-pair':header='first: "'+'A'*1_999_850+'"'
    elif kind=='opaque-semantic-boundary':header='first: &same '+binary(b'A'*999994)+'\nsecond: *same'
    elif kind=='Unicode-binary-key':header=binary('first')+': "'+'漢'*666600+'"'
    elif kind=='aliased-Unicode-binary-keys':header=binary('first')+': &same "'+'漢'*666600+'"\n'+binary('other')+': *same\n'+binary('third')+': *same'
    else:header=escaped('漢'*150)+': '+binary(b'\xff'+b'A'*1498999)
    path,raw,rows=original(header);observed=measure_source_work(monkeypatch)
    assert preflight(rows,observed)=='accepted' and path.read_bytes()==raw

@pytest.mark.parametrize('keykind',['string','binary'])
def test_cached_numeric_view_is_still_screened_under_each_sensitive_key(brain,monkeypatch,keykind):
    secret=escaped('password') if keykind=='string' else binary('password')
    path,raw,rows=original('first: &same '+'1'*4000+'\n'+secret+': *same\nlast: *same')
    observed=measure_source_work(monkeypatch)
    assert preflight(rows,observed,allow_attention=True)=='attention' and path.read_bytes()==raw

@pytest.mark.parametrize('shape',['mapping','pairs'])
def test_numeric_caches_do_not_hide_actual_graph_cycles(brain,monkeypatch,shape):
    header='outer: &same {number: 1, loop: *same}' if shape=='mapping' else 'outer: &same [!!pairs [{number: 1}], *same]'
    path,raw,rows=original(header);observed=measure_source_work(monkeypatch)
    assert preflight(rows,observed,allow_attention=True)=='attention' and path.read_bytes()==raw

@pytest.mark.parametrize('boundary',['nodes','depth'])
def test_numeric_cache_and_work_events_preserve_graph_node_and_depth_limits(brain,boundary):
    value=int('1'*4000)
    if boundary=='nodes':
        graph={'a':value,'b':value};w._screen_source_metadata(graph,max_nodes=5)
        with pytest.raises(ValueError):w._screen_source_metadata(graph,max_nodes=4)
    else:
        graph={'annotation':[('label',value)]};w._screen_source_metadata(graph,max_depth=3)
        with pytest.raises(ValueError):w._screen_source_metadata(graph,max_depth=2)
