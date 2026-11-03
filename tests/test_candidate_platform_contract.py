"""Candidate OS/architecture admission, with explicit native syscall boundaries."""
import ast
import builtins
import ctypes
import errno
import importlib.util
import os
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import pytest
import brain_service as service


SOURCE=Path(__file__).resolve().parents[1]


class NativeCall:
    def __init__(self,callback):self.callback=callback
    def __call__(self,*args):return self.callback(*args)


@pytest.fixture(params=['install','update','service'])
def entry(request):
    if request.param=='service':return service
    path=SOURCE/(request.param+'.py')
    spec=importlib.util.spec_from_file_location('admission_'+request.param,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def boundary(monkeypatch,entry,platform,machine,*,pointer=8,process=0,native=0x8664,
             translated=0,query_result=0,query_errno=0,size=4,windows_success=True):
    monkeypatch.setattr(entry,'sys',SimpleNamespace(platform=platform))
    monkeypatch.setattr(struct,'calcsize',lambda code:pointer)
    monkeypatch.setattr(os,'uname',lambda:SimpleNamespace(machine=machine),raising=False)
    def windows_query(handle,process_out,native_out):
        process_out._obj.value=process;native_out._obj.value=native
        return windows_success
    windows=SimpleNamespace(GetCurrentProcess=NativeCall(lambda:-1),IsWow64Process2=NativeCall(windows_query))
    monkeypatch.setattr(ctypes,'WinDLL',lambda *a,**k:windows,raising=False)
    def mac_query(name,value,size_out,new,new_size):
        assert name==b'sysctl.proc_translated' and new is None and new_size==0
        value._obj.value=translated;size_out._obj.value=size
        return query_result
    monkeypatch.setattr(ctypes,'CDLL',lambda *a,**k:SimpleNamespace(sysctlbyname=NativeCall(mac_query)))
    monkeypatch.setattr(ctypes,'get_errno',lambda:query_errno)


@pytest.mark.parametrize('platform,machine',[
    ('win32','unused'),('linux','x86_64'),('darwin','arm64'),('darwin','x86_64')])
def test_exact_candidate_matrix_accepts_native_boundary_only(entry,monkeypatch,platform,machine):
    boundary(monkeypatch,entry,platform,machine)
    assert entry._candidate_platform() is None
    guard=entry._qualified_native_platform if entry is service else entry._qualified_platform
    assert guard() is None


@pytest.mark.parametrize('platform,machine,changes',[
    ('freebsd14','x86_64',{}),('linux','aarch64',{}),('linux','amd64',{}),
    ('darwin','arm64e',{}),('darwin','x86_64',{'translated':1}),
    ('darwin','arm64',{'query_result':-1,'query_errno':errno.EPERM}),
    ('darwin','arm64',{'query_result':1}),('darwin','arm64',{'size':8}),
    ('win32','unused',{'process':0x8664,'native':0xaa64}),
    ('win32','unused',{'native':0xaa64}),('win32','unused',{'process':0x14c}),
    ('win32','unused',{'windows_success':False}),('linux','x86_64',{'pointer':4})])
def test_unsupported_or_uncertain_matrix_refuses(entry,monkeypatch,platform,machine,changes):
    boundary(monkeypatch,entry,platform,machine,**changes)
    with pytest.raises(ValueError):entry._candidate_platform()


@pytest.mark.parametrize('machine',['arm64','x86_64'])
def test_apple_documented_missing_translation_key_means_native(entry,monkeypatch,machine):
    boundary(monkeypatch,entry,'darwin',machine,query_result=-1,query_errno=errno.ENOENT)
    assert entry._candidate_platform() is None


@pytest.mark.parametrize('platform,machine',[('win32','unused'),('darwin','arm64')])
@pytest.mark.parametrize('failure',['load','query','argument','import'])
def test_native_query_unavailability_converts_to_refusal(entry,monkeypatch,platform,machine,failure):
    boundary(monkeypatch,entry,platform,machine)
    attribute='WinDLL' if platform=='win32' else 'CDLL'
    if failure=='load':
        def fail(*a,**k):raise OSError('synthetic unavailable native library')
        monkeypatch.setattr(ctypes,attribute,fail)
    elif failure in ('query','argument'):
        def fail(*a,**k):
            if failure=='argument':raise ctypes.ArgumentError('synthetic ABI refusal')
            raise OSError('synthetic query failure')
        if platform=='win32':
            result=SimpleNamespace(GetCurrentProcess=NativeCall(lambda:-1),IsWow64Process2=NativeCall(fail))
        else:result=SimpleNamespace(sysctlbyname=NativeCall(fail))
        monkeypatch.setattr(ctypes,attribute,lambda *a,**k:result)
    else:
        original=builtins.__import__
        def missing(name,*a,**k):
            if name=='ctypes':raise ImportError('synthetic missing optional ctypes')
            return original(name,*a,**k)
        monkeypatch.setattr(builtins,'__import__',missing)
    with pytest.raises(ValueError):entry._candidate_platform()


def test_linux_admission_does_not_require_ctypes(entry,monkeypatch):
    boundary(monkeypatch,entry,'linux','x86_64')
    original=builtins.__import__
    def missing(name,*a,**k):
        if name=='ctypes':raise AssertionError('Linux has no native ctypes admission dependency')
        return original(name,*a,**k)
    monkeypatch.setattr(builtins,'__import__',missing)
    assert entry._candidate_platform() is None


def test_pointer_refusal_precedes_any_native_query(entry,monkeypatch):
    boundary(monkeypatch,entry,'darwin','arm64',pointer=4)
    monkeypatch.setattr(os,'uname',lambda:pytest.fail('32-bit admission reached uname'))
    monkeypatch.setattr(ctypes,'CDLL',lambda *a,**k:pytest.fail('32-bit admission reached native query'))
    with pytest.raises(ValueError,match='64-bit'):entry._candidate_platform()


def test_missing_uname_refuses(entry,monkeypatch):
    boundary(monkeypatch,entry,'linux','x86_64')
    monkeypatch.delattr(os,'uname')
    with pytest.raises(ValueError,match='architecture is unavailable'):entry._candidate_platform()


def test_three_isolated_admission_copies_have_identical_ast():
    bodies=[]
    for name in ('install.py','update.py','app/brain_service.py'):
        tree=ast.parse((SOURCE/name).read_text())
        functions=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='_candidate_platform']
        assert len(functions)==1
        bodies.append(ast.dump(functions[0],include_attributes=False))
    assert len(set(bodies))==1
