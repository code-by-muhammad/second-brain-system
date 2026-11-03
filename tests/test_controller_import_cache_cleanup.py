"""Controller import-cache cleanup is empty-only and scoped to this child invocation."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
import brain_settings as cfg


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

@pytest.fixture
def boundary(brain,monkeypatch):
    checkout=Path(__file__).resolve().parents[1]
    update=load('cache_update',checkout/'update.py');support=load('cache_support',checkout/'setup_runtime.py')
    monkeypatch.setattr(update,'installer',lambda:SimpleNamespace(setup_helpers=lambda:support))
    monkeypatch.setattr(update,'cli_environment',lambda root:{})
    return update,support

@pytest.mark.parametrize('outcome',['success','refused','malformed','transport-error'])
def test_controller_removes_only_its_empty_cache_after_contained_run(boundary,monkeypatch,outcome):
    update,support=boundary;seen=[]
    parent=cfg.ROOT/'state/import-cache';parent.mkdir();old=parent/'run-previous';old.mkdir()
    def run(arguments,**kwargs):
        assert arguments[1:3]==['-I','-B'] and arguments[3:6]==['-X','utf8','-X']
        cache=Path(arguments[6].removeprefix('pycache_prefix='));seen.append(cache)
        assert cache.parent==parent and cache!=old and cache.is_dir() and not list(cache.iterdir())
        if outcome=='transport-error':raise OSError('Synthetic contained run failure')
        return SimpleNamespace(returncode=9 if outcome=='refused' else 0,stdout='not-json' if outcome=='malformed' else '{"status":"stopped"}')
    monkeypatch.setattr(support,'run',run)
    if outcome=='success':assert update.controller(cfg.PYTHON,cfg.ROOT,cfg.ROOT,'service','inspect')=={'status':'stopped'}
    else:
        error=OSError if outcome=='transport-error' else RuntimeError if outcome=='refused' else ValueError
        with pytest.raises(error):update.controller(cfg.PYTHON,cfg.ROOT,cfg.ROOT,'service','inspect')
    assert len(seen)==1 and not seen[0].exists() and old.is_dir()


def test_repeated_controller_polls_keep_unique_prefix_without_empty_accumulation(boundary,monkeypatch):
    update,support=boundary;seen=[]
    def run(arguments,**kwargs):
        seen.append(arguments[6]);return SimpleNamespace(returncode=0,stdout='{}')
    monkeypatch.setattr(support,'run',run)
    for _ in range(3):update.controller(cfg.PYTHON,cfg.ROOT,cfg.ROOT,'health')
    assert len(set(seen))==3 and list((cfg.ROOT/'state/import-cache').iterdir())==[]


def test_nonempty_cache_and_foreign_siblings_are_preserved(boundary,monkeypatch):
    update,support=boundary;seen=[]
    parent=cfg.ROOT/'state/import-cache';parent.mkdir();foreign=parent/'run-foreign';foreign.mkdir();(foreign/'owner').write_bytes(b'keep')
    def run(arguments,**kwargs):
        cache=Path(arguments[6].removeprefix('pycache_prefix='));seen.append(cache)
        (cache/'owner').write_bytes(b'keep nonempty');return SimpleNamespace(returncode=0,stdout='{}')
    monkeypatch.setattr(support,'run',run);update.controller(cfg.PYTHON,cfg.ROOT,cfg.ROOT,'health')
    assert (seen[0]/'owner').read_bytes()==b'keep nonempty' and (foreign/'owner').read_bytes()==b'keep'

@pytest.mark.parametrize('kind',['foreign-parent','foreign-name','link','junction','replaced-identity','resolve-uncertainty'])
def test_foreign_linked_or_replaced_cache_is_never_removed(boundary,monkeypatch,kind):
    update,support=boundary;selected=[];original=support.isolated_python
    def isolated(python,root):
        args=original(python,root);cache=Path(args[-1].removeprefix('pycache_prefix='))
        if kind in ('foreign-parent','foreign-name'):
            cache.rmdir();cache=(cfg.ROOT/'state/foreign/run-owned-name' if kind=='foreign-parent' else cache.parent/'other-owner')
            cache.mkdir(parents=True);args[-1]='pycache_prefix='+str(cache)
        selected.append(cache);return args
    monkeypatch.setattr(support,'isolated_python',isolated)
    def run(arguments,**kwargs):
        cache=selected[0]
        if kind in ('link','junction'):
            attribute='is_symlink' if kind=='link' else 'is_junction';actual=getattr(Path,attribute)
            monkeypatch.setattr(Path,attribute,lambda path:path==cache or actual(path))
        elif kind=='resolve-uncertainty':
            actual=Path.resolve
            def resolve(path,*args,**kwargs):
                if path==cache:raise RuntimeError('Synthetic replaced symlink loop')
                return actual(path,*args,**kwargs)
            monkeypatch.setattr(Path,'resolve',resolve)
        elif kind=='replaced-identity':
            actual=Path.stat
            def stat(path,*args,**kwargs):
                result=actual(path,*args,**kwargs)
                return SimpleNamespace(st_dev=result.st_dev,st_ino=result.st_ino+1,st_mode=result.st_mode) if path==cache and kwargs.get('follow_symlinks') is False else result
            monkeypatch.setattr(Path,'stat',stat)
        return SimpleNamespace(returncode=0,stdout='{}')
    monkeypatch.setattr(support,'run',run);update.controller(cfg.PYTHON,cfg.ROOT,cfg.ROOT,'health')
    assert selected[0].is_dir()
