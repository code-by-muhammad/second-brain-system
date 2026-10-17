"""Actual health function body and cache reader, only synthetic local files.

The server function is compiled from its unchanged AST body without registering
MCP or reading authentication. Existing MCP integration tests retain that gate.
"""
import ast
import hashlib
import json
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


def fault_reader(monkeypatch, path, mode):
    if mode == 'read-error':
        original = Path.read_text
        def read(self,*args,**kwargs):
            if self == path:
                raise OSError('Synthetic private cache detail.')
            return original(self,*args,**kwargs)
        monkeypatch.setattr(Path,'read_text',read)
    elif mode in ('recursion','runtime','memory'):
        original = json.loads
        error = {'recursion':RecursionError,'runtime':RuntimeError,'memory':MemoryError}[mode]
        def loads(value,*args,**kwargs):
            if value == '{"synthetic_cache_fault":true}':
                raise error('Synthetic private decoder detail.')
            return original(value,*args,**kwargs)
        monkeypatch.setattr(json,'loads',loads)


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
    assert result['version'] == cfg.VERSION and result['wiki'] == {'status':'idle'}
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
