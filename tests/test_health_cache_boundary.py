"""Actual health function body and cache reader, only synthetic local files.

The server function is compiled from its unchanged AST body without registering
MCP or reading authentication. Existing MCP integration tests retain that gate.
"""
import ast
import hashlib
import json
import os
from pathlib import Path

import pytest
import brain_maintenance as maintenance
import brain_settings as cfg
import brain_store as store
import brain_wiki as wiki


def health_function():
    source = Path(cfg.__file__).with_name('brain_server.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'brain_health']
    assert len(functions) == 1
    function = functions[0]
    function.decorator_list = []
    namespace = {'store':store, 'cfg':cfg, 'json':json}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),str(source),'exec'),namespace)
    return namespace['brain_health']

@pytest.mark.parametrize('component',['wiki','index'])
@pytest.mark.parametrize('error',[MemoryError,RuntimeError])
def test_fatal_health_failures_remain_visible(brain,monkeypatch,component,error):
    def fail(*args,**kwargs):raise error('Synthetic internal fault')
    if component=='wiki':monkeypatch.setattr(wiki,'_worker_summary',fail)
    else:
        import sqlite3
        monkeypatch.setattr(sqlite3,'connect',fail)
    with pytest.raises(error):health_function()()


def fault_reader(monkeypatch, path, mode):
    if mode == 'read-error':
        original = Path.open;seen=[]
        def read(self,*args,**kwargs):
            if self == path and not seen:
                seen.append(True)
                raise OSError('Synthetic private cache detail.')
            return original(self,*args,**kwargs)
        monkeypatch.setattr(Path,'open',read)
        if os.name!='nt':
            native_open=os.open
            def read_native(file,*args,**kwargs):
                if Path(file)==path and not seen:
                    seen.append(True);raise OSError('Synthetic private cache detail.')
                return native_open(file,*args,**kwargs)
            monkeypatch.setattr(os,'open',read_native)
    elif mode in ('recursion','runtime','memory'):
        original = json.loads
        error = {'recursion':RecursionError,'runtime':RuntimeError,'memory':MemoryError}[mode]
        def loads(value,*args,**kwargs):
            if value in ('{"synthetic_cache_fault":true}',b'{"synthetic_cache_fault":true}'):
                raise error('Synthetic private decoder detail.')
            return original(value,*args,**kwargs)
        monkeypatch.setattr(json,'loads',loads)

@pytest.mark.parametrize('component',['worker','maintenance','bootstrap'])
@pytest.mark.parametrize('kind',['symlink','junction'])
def test_dangling_cache_links_report_attention_without_open(brain,monkeypatch,component,kind):
    names={'worker':'wiki-worker-status.json','maintenance':'maintenance-status.json','bootstrap':'index-bootstrap-status.json'}
    path=brain[1]/names[component]
    assert not path.exists()
    method='is_symlink' if kind=='symlink' else 'is_junction';original=getattr(Path,method)
    monkeypatch.setattr(Path,method,lambda self:self==path or original(self))
    original_open=Path.open;native_open=os.open
    def no_open(self,*args,**kwargs):
        if self==path:pytest.fail('Dangling cache link must not be opened')
        return original_open(self,*args,**kwargs)
    def no_native(file,*args,**kwargs):
        if Path(file)==path:pytest.fail('Dangling cache link must not be opened')
        return native_open(file,*args,**kwargs)
    monkeypatch.setattr(Path,'open',no_open)
    if os.name!='nt':monkeypatch.setattr(os,'open',no_native)
    value=health_function()()
    if component=='worker':assert value['wiki']['worker']['attention_required'] is True
    else:assert value['maintenance' if component=='maintenance' else 'initial_index_validation']['status']=='needs attention'


@pytest.mark.parametrize('component', ['maintenance','bootstrap'])
@pytest.mark.parametrize('mode,raw', [
    ('malformed',b'{'), ('read-error',b'{"status":"completed"}'),
    ('invalid-utf8',b'\xffinvalid'), ('recursion',b'{"synthetic_cache_fault":true}'),
    ('null',b'null'), ('list',b'[]'), ('string',b'"verified"'),
], ids=['malformed','read-error','invalid-utf8','decoder-recursion','null','list','string'])
def test_component_cache_failure_keeps_full_health_available_and_original_bytes(brain,monkeypatch,component,mode,raw):
    path = brain[1]/('maintenance-status.json' if component == 'maintenance' else 'index-bootstrap-status.json')
    path.write_bytes(raw)
    before = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(wiki,'status',lambda:{'status':'idle'})
    fault_reader(monkeypatch,path,mode)
    result = health_function()()
    value = result['maintenance' if component == 'maintenance' else 'initial_index_validation']
    assert isinstance(value,dict) and value['status'] == 'needs attention'
    assert result['version'] == cfg.VERSION and result['wiki']['worker']['status']=='idle'
    assert result['wiki']['coverage'].startswith('not checked')
    assert 'index' in result
    assert 'Synthetic private' not in json.dumps(result)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('component', ['maintenance','bootstrap'])
def test_valid_component_cache_keeps_existing_health_fields(brain,monkeypatch,component):
    path = brain[1]/('maintenance-status.json' if component == 'maintenance' else 'index-bootstrap-status.json')
    expected = {'status':'completed','captures_linked':2,'review':[]} if component == 'maintenance' else {'status':'verified','counts':{'entity':3}}
    raw = json.dumps(expected).encode('utf-8')
    path.write_bytes(raw)
    monkeypatch.setattr(wiki,'status',lambda:{'status':'idle'})
    result = health_function()()
    value = result['maintenance' if component == 'maintenance' else 'initial_index_validation']
    assert value['status'] == expected['status']
    if component == 'maintenance':
        assert value['captures_linked'] == 2 and value['review_count'] == 0
    else:
        assert value == expected
    assert path.read_bytes() == raw


def test_missing_optional_caches_keep_health_available_without_creating_them(brain,monkeypatch):
    monkeypatch.setattr(wiki,'status',lambda:{'status':'idle'})
    result = health_function()()
    assert 'maintenance' not in result and 'initial_index_validation' not in result
    assert list(brain[1].iterdir()) == []


@pytest.mark.parametrize('component', ['maintenance','bootstrap'])
@pytest.mark.parametrize('mode,error', [('runtime',RuntimeError),('memory',MemoryError)],ids=['runtime','memory'])
def test_unrelated_and_fatal_decoder_errors_remain_visible(brain,monkeypatch,component,mode,error):
    path = brain[1]/('maintenance-status.json' if component == 'maintenance' else 'index-bootstrap-status.json')
    raw = b'{"synthetic_cache_fault":true}'
    path.write_bytes(raw)
    monkeypatch.setattr(wiki,'status',lambda:{'status':'idle'})
    fault_reader(monkeypatch,path,mode)
    with pytest.raises(error,match='Synthetic private decoder'):
        health_function()()
    assert path.read_bytes() == raw
