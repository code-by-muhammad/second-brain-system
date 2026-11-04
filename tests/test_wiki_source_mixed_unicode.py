"""Mixed known Unicode/binary YAML retains privacy and bounded source work."""
import base64,json,re
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_remediation import fake_model
from test_wiki_compact_aggregate_budget import QUOTE,skip_output

VALUE='SyntheticAssociationValue1234567890'
def escaped(value):return '"'+''.join('\\u'+format(ord(c),'04x') for c in value)+'"'
def binary(value):return '!!binary "'+base64.b64encode(value).decode('ascii')+'"'
def header_pair(key,value):
    return (binary(key) if isinstance(key,bytes) else escaped(key))+': '+(binary(value) if isinstance(value,bytes) else escaped(value))
def original(header):
    path=s.VAULT/'01-Projects/Example.md';path.parent.mkdir(parents=True)
    raw=('---\n'+header+'\n---\n# Example Project\n'+QUOTE+'\n').encode('utf-8')
    assert len(raw)<2_000_000 and not s.SECRET.search(raw.decode('utf-8'))
    path.write_bytes(raw);rows=w.inventory();assert rows
    return path,raw,rows
def provider(monkeypatch,rows):
    output=skip_output();text=rows[0]['text']
    output['dispositions'][0]['quote']=QUOTE if QUOTE in text else text[-40:].strip()
    return fake_model(monkeypatch,output)
def attention(path,raw,rows,calls):
    report=w.run(1)
    assert report['status']=='attention' and report['integrity_field']=='content' and not calls
    assert report['integrity_page']=='01-Projects/Example.md' and report['consecutive_failures']==0
    assert path.read_bytes()==raw and w.inventory()==rows
    assert w.status()['pending_source_parts']==len(rows)
    assert not list((cfg.STATE/'wiki/batches').glob('*/input.json'))
    assert VALUE not in json.dumps({'report':report,'worker':w.status()['worker']})

@pytest.mark.parametrize('key,value',[
    ('pa\u017f\u017fword',VALUE.encode()),
    ('password\u00a0',VALUE.encode()),
    ('ap\u0130_key',VALUE.encode()),
    ('access_token\u2003',VALUE.encode()),
    (b'password','\u00a0'+VALUE),
    (b'api_key','\u2003'+VALUE),
    (b'secret','\u202f"'+VALUE),
    ('password',VALUE.encode()),
],ids=['long-s-key','NBSP-key','Unicode-I-key','em-space-key','NBSP-value','em-space-value','narrow-space-quoted-value','ASCII-control'])
def test_mixed_known_strings_keep_complete_unicode_detector_semantics_before_provider(brain,monkeypatch,key,value):
    left=key.decode('ascii') if isinstance(key,bytes) else key
    right=value.decode('ascii') if isinstance(value,bytes) else value
    assert s.SECRET.search(left+': '+right)
    path,raw,rows=original(header_pair(key,value));decoded=w.meta(raw.decode('utf-8'))
    assert next(iter(decoded))==key and decoded[key]==value
    calls=provider(monkeypatch,rows);attention(path,raw,rows,calls)

@pytest.mark.parametrize('shape',['pairs','omap','nested','aliased-mapping'])
def test_mixed_associations_are_screened_in_typed_pairs_and_nested_aliases(brain,monkeypatch,shape):
    pair=header_pair('pa\u017f\u017fword',VALUE.encode())
    if shape in ('pairs','omap'):header='annotation: !!'+shape+' [{'+pair+'}]'
    elif shape=='nested':header='outer:\n  - annotation: {'+pair+'}'
    else:header='first: &same {'+pair+'}\nsecond: *same'
    path,raw,rows=original(header);calls=provider(monkeypatch,rows)
    attention(path,raw,rows,calls)

@pytest.mark.parametrize('direction',['Unicode-key','Unicode-value'])
@pytest.mark.parametrize('mode',['outside-first-chunk','legacy-unused-packet'])
def test_mixed_privacy_is_rechecked_beyond_chunk_and_in_unused_saved_packet(brain,monkeypatch,direction,mode):
    pair=header_pair('pa\u017f\u017fword',VALUE.encode()) if direction=='Unicode-key' else header_pair(b'password','\u00a0'+VALUE)
    header=('padding: "'+'A'*30000+'"\n' if mode=='outside-first-chunk' else '')+pair
    path,raw,rows=original(header);saved=None
    if mode=='outside-first-chunk':assert '\\u017f' not in rows[0]['text'] and '\\u00a0' not in rows[0]['text']
    else:
        with monkeypatch.context() as legacy:
            legacy.setattr(w,'_validate_source_context',lambda packet:None);packet=w.prepare(1)
        folder=cfg.STATE/'wiki/batches'/packet['batch_id'];saved=(folder/'input.json').read_bytes()
    calls=provider(monkeypatch,rows)
    if saved is None:attention(path,raw,rows,calls)
    else:
        report=w.run(1)
        assert not calls and report['status']=='attention' and report['integrity_field']=='content'
        assert report['integrity_page']=='01-Projects/Example.md' and report['consecutive_failures']==0
        assert path.read_bytes()==raw and (folder/'input.json').read_bytes()==saved and w.inventory()==rows
        assert w.status()['pending_source_parts']==len(rows)

@pytest.mark.parametrize('key,value',[
    ('pa\u017f\u017fword',b'rotate safely'),
    (b'password','\u00a0rotate safely'),
    ('ordinary',b'\xff\xfe\x80ordinary'),
    ('password',b'\xff'+VALUE.encode()),
    ('password',b'\xc2\xa0'+VALUE.encode()),
    (b'pa\xc5\xbf\xc5\xbfword',VALUE.encode()),
    (b'password\xc2\xa0',VALUE.encode()),
    (b'ordinary','Unicode \u6f22 annotation'),
],ids=['short-binary-value','short-Unicode-value','opaque-nonUTF8','opaque-prefix-not-removed','binary-NBSP-not-decoded','all-binary-long-s-not-decoded','all-binary-NBSP-not-decoded','ordinary-Unicode'])
def test_opaque_binary_and_benign_mixed_pairs_keep_original_compilation(brain,monkeypatch,key,value):
    path,raw,rows=original(header_pair(key,value));calls=provider(monkeypatch,rows);report=w.run(1)
    assert report['status']=='complete' and len(calls)==1 and path.read_bytes()==raw

@pytest.mark.parametrize('header',[
    header_pair('ordinary',b'opaque\x00annotation'),
    header_pair(b'ordinary','opaque\x00annotation'),
    header_pair(b'ordinary','opaque\ud800annotation'),
    'outer: &same ['+binary(b'ordinary')+', *same]',
],ids=['binary-NUL','Unicode-NUL','Unicode-surrogate','mixed-cycle'])
def test_mixed_views_do_not_weaken_existing_scalar_or_cycle_guards(brain,monkeypatch,header):
    path,raw,rows=original(header);calls=provider(monkeypatch,rows);attention(path,raw,rows,calls)

def measured(monkeypatch):
    observed={'units':0,'byte_views':0,'byte_view_units':0};actual=s.SECRET
    class Pattern:
        pattern=actual.pattern;flags=actual.flags
        def search(self,value,*args,**kwargs):
            observed['units']+=len(value);assert observed['units']+observed['byte_view_units']<=8_000_000
            return actual.search(value,*args,**kwargs)
    class BinaryPattern:
        def __init__(self,compiled):self.compiled=compiled
        def search(self,value,*args,**kwargs):
            observed['units']+=len(value);assert observed['units']+observed['byte_view_units']<=8_000_000
            return self.compiled.search(value,*args,**kwargs)
    class Regex:
        def __getattr__(self,name):return getattr(re,name)
        def compile(self,pattern,flags=0):return BinaryPattern(re.compile(pattern,flags))
    class TrackedBytes(bytes):
        def decode(self,*args,**kwargs):
            observed['byte_views']+=1;observed['byte_view_units']+=len(self)
            assert observed['units']+observed['byte_view_units']<=8_000_000
            return super().decode(*args,**kwargs)
    monkeypatch.setattr(s,'SECRET',Pattern());monkeypatch.setattr(w,'re',Regex())
    return observed,TrackedBytes

@pytest.mark.parametrize('aliases',[0,1,20,1000])
def test_mixed_binary_aliases_have_one_cached_view_and_a_bounded_work_envelope(brain,monkeypatch,aliases):
    observed,tracked=measured(monkeypatch);value=tracked(b'\xff'+b'A'*699999)
    graph={('label'+str(i)):value for i in range(aliases+1)}
    if aliases<=1:w._screen_source_metadata(graph)
    else:
        with pytest.raises(ValueError,match='text bounds'):w._screen_source_metadata(graph)
    assert observed['units']+observed['byte_view_units']<=8_000_000 and observed['byte_views']==1

@pytest.mark.parametrize('kind',['semantic-fitting','semantic-overflow','Unicode-UTF8-fitting','node-boundary','depth-boundary','cached-sensitive-key'])
def test_mixed_view_caches_preserve_semantic_structural_and_association_boundaries(brain,monkeypatch,kind):
    observed,tracked=measured(monkeypatch)
    if kind.startswith('semantic'):
        value=tracked(b'\xff'+b'A'*999993);graph=[('first',value),('other',value)]
        if kind=='semantic-fitting':w._screen_source_metadata(graph)
        else:
            graph.append('AAA')
            with pytest.raises(ValueError,match='text bounds'):w._screen_source_metadata(graph)
    elif kind=='Unicode-UTF8-fitting':w._screen_source_metadata({tracked(b'a'):'\u6f22'*666600})
    elif kind=='node-boundary':
        graph={tracked(b'a'):'ordinary'};w._screen_source_metadata(graph,max_nodes=3)
        with pytest.raises(ValueError,match='structural bounds'):w._screen_source_metadata(graph,max_nodes=2)
    elif kind=='depth-boundary':
        graph={'annotation':[(tracked(b'a'),'ordinary')]};w._screen_source_metadata(graph,max_depth=3)
        with pytest.raises(ValueError,match='structural bounds'):w._screen_source_metadata(graph,max_depth=2)
    else:
        value=tracked(VALUE.encode());graph={'ordinary':value,'pa\u017f\u017fword':value}
        with pytest.raises(ValueError,match='credential|Unsafe'):w._screen_source_metadata(graph)
    assert observed['units']+observed['byte_view_units']<=8_000_000
