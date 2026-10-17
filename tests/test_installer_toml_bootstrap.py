"""Synthetic installer/parser preflight contracts; no actual clients or services."""
import builtins
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest

SOURCE=Path(os.environ.get('R133_SOURCE',Path(__file__).resolve().parents[1])).resolve()
spec=importlib.util.spec_from_file_location('r133_bootstrap_installer',SOURCE/'install.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
spec=importlib.util.spec_from_file_location('r133_bootstrap_updater',SOURCE/'update.py')
updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)


def tree(root):
    return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}


def context(tmp_path,monkeypatch,text='model = "owner"\n'):
    home=tmp_path/'fake owner home';(home/'.codex').mkdir(parents=True)
    appdata=tmp_path/'fake appdata';appdata.mkdir()
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home));monkeypatch.setenv('APPDATA',str(appdata))
    (home/'.codex/config.toml').write_text(text,encoding='utf-8')
    (home/'.claude.json').write_text('{"owner":"preserved"}\n',encoding='utf-8')
    vault=tmp_path/'synthetic vault';vault.mkdir()
    root=tmp_path/'synthetic runtime'
    python=tmp_path/'selected environment with spaces'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    python.parent.mkdir(parents=True);python.write_bytes(b'fixture executable never launched')
    def forbidden(*args,**kwargs):raise AssertionError('Unexpected client/runtime write or process.')
    for name in ('atomic','private_dir','backup','clients'):monkeypatch.setattr(installer,name,forbidden)
    return home,appdata,vault,root,python,forbidden


def planned(home,root,python):
    return next(value for path,value in installer.client_plan(root,python,home) if path==home/'.codex/config.toml')


def missing_parser(monkeypatch,mode):
    original=builtins.__import__
    def invoke(name,*args,**kwargs):
        if name=='tomlkit':
            if mode=='missing':raise ModuleNotFoundError('synthetic absent locked parser',name='tomlkit')
            return SimpleNamespace(__version__='0.14.0')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',invoke)


OWNER_VALUES=[
    ('nan','owner_nan = nan\n'),
    ('infinity','positive = +inf\nnegative = -inf\nsigned_zero = -0.0\n'),
    ('date-time','date = 2026-10-17\ntime = 10:20:30.125\nstamp = 2026-10-17T10:20:30+05:00\n'),
    ('nested-nan','owner = [{reading = nan, active = true, count = 1}]\n'),
    ('typed-array','owner = [true, 1, 1.5, "1", {nested = [2, 3]}]\n'),
    ('escaped-literals','owner = "line\\n\\\"quoted\\\""\nliteral = \'[mcp_servers.second-brain]\'\n'),
]


@pytest.mark.parametrize('kind,text',OWNER_VALUES,ids=[kind for kind,_ in OWNER_VALUES])
def test_typed_unrelated_owner_values_survive_logical_server_replacement(tmp_path,monkeypatch,kind,text):
    original=tomllib.loads(text)
    home,appdata,_,root,python,forbidden=context(tmp_path,monkeypatch,'# owner comment\n'+text)
    monkeypatch.setattr(installer.subprocess,'run',forbidden)
    before=(tree(home),tree(appdata))
    output=planned(home,root,python);actual=tomllib.loads(output)
    assert actual['mcp_servers']['second-brain']=={'command':str(python),'args':[str(root/'app/brain_client.py')],'startup_timeout_sec':60,'tool_timeout_sec':120}
    del actual['mcp_servers']
    if kind=='nan':assert math.isnan(actual['owner_nan'])
    elif kind=='nested-nan':
        assert math.isnan(actual['owner'][0]['reading'])
        assert actual['owner'][0]['active'] is True and type(actual['owner'][0]['count']) is int
    else:assert actual==original
    if kind=='infinity':assert math.copysign(1,actual['signed_zero'])==-1
    if kind=='typed-array':assert [type(v) for v in actual['owner'][:4]]==[bool,int,float,str]
    assert '# owner comment' in output and (tree(home),tree(appdata))==before


@pytest.mark.parametrize('value',['"owner-scalar"','1','[1, 2]','true'],ids=['string','integer','array','boolean'])
def test_nonmapping_owner_mcp_parent_is_rejected_without_overwrite(tmp_path,monkeypatch,value):
    home,appdata,_,root,python,forbidden=context(tmp_path,monkeypatch,'mcp_servers = '+value+'\n')
    monkeypatch.setattr(installer.subprocess,'run',forbidden);before=(tree(home),tree(appdata))
    with pytest.raises(ValueError):planned(home,root,python)
    assert (tree(home),tree(appdata))==before and not root.exists()


@pytest.mark.parametrize('value',['"obsolete"','12','["obsolete"]'],ids=['string','integer','array'])
def test_whole_managed_scalar_value_is_replaced_while_sibling_is_preserved(tmp_path,monkeypatch,value):
    text='mcp_servers.second-brain = '+value+'\nmcp_servers.other.command = "keep"\n'
    home,appdata,_,root,python,forbidden=context(tmp_path,monkeypatch,text)
    monkeypatch.setattr(installer.subprocess,'run',forbidden);before=(tree(home),tree(appdata))
    output=tomllib.loads(planned(home,root,python))
    assert output['mcp_servers']['other']=={'command':'keep'}
    assert output['mcp_servers']['second-brain']=={'command':str(python),'args':[str(root/'app/brain_client.py')],'startup_timeout_sec':60,'tool_timeout_sec':120}
    assert (tree(home),tree(appdata))==before


@pytest.mark.parametrize('mode',['missing','wrong-version'])
def test_unavailable_locked_parser_is_a_controlled_no_write_preflight_error(tmp_path,monkeypatch,mode):
    home,appdata,_,root,python,forbidden=context(tmp_path,monkeypatch)
    monkeypatch.setattr(installer.subprocess,'run',forbidden);missing_parser(monkeypatch,mode)
    before=(tree(home),tree(appdata))
    with pytest.raises(ValueError):planned(home,root,python)
    assert (tree(home),tree(appdata))==before and not root.exists()


def normal_args(monkeypatch,root,vault,*extra):
    argv=['install.py','--root',str(root),'--vault',str(vault),'--configure-clients',*extra]
    monkeypatch.setattr(sys,'argv',argv)
    return argv


@pytest.mark.parametrize('child_fails',[False,True],ids=['successful-child','failed-child'])
def test_normal_bootstrap_hands_off_original_arguments_once_before_deploy(tmp_path,monkeypatch,child_fails):
    home,appdata,vault,root,_,forbidden=context(tmp_path,monkeypatch)
    release=tmp_path/'verified project with spaces';release.mkdir();monkeypatch.setattr(installer,'REPO',release)
    python=release/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
    argv=normal_args(monkeypatch,root,vault,'--download-model','--role','capture')
    calls=[];before=(tree(home),tree(appdata));monkeypatch.setattr(installer,'deploy',forbidden)
    def run(args,**kwargs):
        calls.append((args,kwargs))
        if args[0]=='uv':python.parent.mkdir(parents=True);python.write_bytes(b'prepared only')
        else:
            assert args==[str(python),str(release/'install.py'),*argv[1:],'--reuse-python',str(python)]
            if child_fails:raise subprocess.CalledProcessError(23,args)
        return subprocess.CompletedProcess(args,0)
    monkeypatch.setattr(installer.subprocess,'run',run)
    with pytest.raises(SystemExit) as error:installer.main()
    assert error.value.code==(23 if child_fails else 0)
    assert len(calls)==2 and all(kwargs=={'check':True} for _,kwargs in calls)
    assert calls[0][0]==['uv','sync','--locked','--no-dev','--project',str(release),'--python','3.12']
    assert (tree(home),tree(appdata))==before and not root.exists()


def test_sync_failure_never_hands_off_or_deploys(tmp_path,monkeypatch):
    home,appdata,vault,root,_,forbidden=context(tmp_path,monkeypatch)
    normal_args(monkeypatch,root,vault);monkeypatch.setattr(installer,'deploy',forbidden);calls=[]
    def run(args,**kwargs):calls.append(args);raise subprocess.CalledProcessError(7,args)
    monkeypatch.setattr(installer.subprocess,'run',run);before=(tree(home),tree(appdata))
    with pytest.raises(subprocess.CalledProcessError):installer.main()
    assert len(calls)==1 and calls[0][0]=='uv' and (tree(home),tree(appdata))==before and not root.exists()


def test_missing_selected_bootstrap_python_stops_before_handoff(tmp_path,monkeypatch):
    home,appdata,vault,root,_,forbidden=context(tmp_path,monkeypatch)
    release=tmp_path/'missing prepared interpreter';release.mkdir();monkeypatch.setattr(installer,'REPO',release)
    normal_args(monkeypatch,root,vault);calls=[];monkeypatch.setattr(installer,'deploy',forbidden)
    monkeypatch.setattr(installer.subprocess,'run',lambda args,**kw:(calls.append(args) or subprocess.CompletedProcess(args,0)))
    before=(tree(home),tree(appdata))
    with pytest.raises(SystemExit) as error:installer.main()
    assert error.value.code==2 and len(calls)==1 and (tree(home),tree(appdata))==before and not root.exists()


@pytest.mark.parametrize('configure',[False,True])
def test_reuse_python_keeps_lexical_selected_path_without_sync_or_handoff(tmp_path,monkeypatch,configure):
    home,appdata,vault,root,python,forbidden=context(tmp_path,monkeypatch)
    monkeypatch.setattr(sys,'argv',['install.py','--root',str(root),'--vault',str(vault),'--reuse-python',str(python),*(['--configure-clients'] if configure else [])])
    monkeypatch.setattr(installer.subprocess,'run',forbidden);calls=[]
    def deploy(*args):calls.append(args);return {'status':'synthetic-no-write'}
    monkeypatch.setattr(installer,'deploy',deploy);before=(tree(home),tree(appdata))
    installer.main()
    assert len(calls)==1 and calls[0]==(root,python.absolute(),vault,'capture',configure)
    assert (tree(home),tree(appdata))==before and not root.exists()


def test_validate_only_reuse_invokes_preflight_without_sync_handoff_or_deploy(tmp_path,monkeypatch):
    home,appdata,vault,root,python,forbidden=context(tmp_path,monkeypatch)
    monkeypatch.setattr(sys,'argv',['install.py','--root',str(root),'--vault',str(vault),'--reuse-python',str(python),'--configure-clients','--validate-only'])
    monkeypatch.setattr(installer.subprocess,'run',forbidden);monkeypatch.setattr(installer,'deploy',forbidden);calls=[]
    monkeypatch.setattr(installer,'validate',lambda *args:calls.append(args))
    before=(tree(home),tree(appdata));installer.main()
    assert calls==[(root,vault,python.absolute(),True)] and (tree(home),tree(appdata))==before and not root.exists()


def test_handoff_reuse_flag_terminates_a_simulated_child_without_a_loop(tmp_path,monkeypatch):
    home,appdata,vault,root,_,_=context(tmp_path,monkeypatch)
    release=tmp_path/'verified child project';release.mkdir();monkeypatch.setattr(installer,'REPO',release)
    python=release/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
    normal_args(monkeypatch,root,vault);calls=[];deployed=[]
    monkeypatch.setattr(installer,'deploy',lambda *args:(deployed.append(args) or {'status':'synthetic'}))
    def run(args,**kwargs):
        calls.append(args)
        assert len(calls)<=2,'Bootstrap handoff loop.'
        if args[0]=='uv':python.parent.mkdir(parents=True);python.write_bytes(b'fixture only')
        else:
            saved=sys.argv
            try:sys.argv=[args[1],*args[2:]];installer.main()
            finally:sys.argv=saved
        return subprocess.CompletedProcess(args,0)
    monkeypatch.setattr(installer.subprocess,'run',run);before=(tree(home),tree(appdata))
    with pytest.raises(SystemExit) as error:installer.main()
    assert error.value.code==0
    assert len(calls)==2 and len(deployed)==1 and deployed[0][1]==python.absolute()
    assert calls[1].count('--reuse-python')==1 and (tree(home),tree(appdata))==before and not root.exists()


@pytest.mark.parametrize('mode',['missing','wrong-version'])
def test_candidate_update_parser_preflight_failure_preserves_old_runtime_and_never_stops(tmp_path,monkeypatch,mode):
    home,appdata,vault,root,python,forbidden=context(tmp_path,monkeypatch)
    (root/'state').mkdir(parents=True);(root/'app').mkdir()
    (root/'app/original.py').write_bytes(b'original runtime bytes')
    (root/'device.json').write_text(json.dumps({'vault':str(vault),'python':str(python),'release_root':'synthetic-old-release','version':'0.1.6','role':'capture'}),encoding='utf-8')
    release=root/'releases/v0.1.7';release.mkdir(parents=True)
    monkeypatch.setattr(updater,'verify',lambda path,**kwargs:{'version':'0.1.7'})
    monkeypatch.setattr(updater,'snapshot',forbidden);monkeypatch.setattr(updater,'restore',forbidden)
    missing_parser(monkeypatch,mode);calls=[]
    def run(args,**kwargs):
        calls.append(args)
        if args[0]=='uv':return subprocess.CompletedProcess(args,0)
        if '--validate-only' in args:
            installer.validate(root,vault,python,True)
            return subprocess.CompletedProcess(args,0)
        raise AssertionError('No stop, install, start or rollback is allowed after failed preflight.')
    monkeypatch.setattr(updater.subprocess,'run',run);before=(tree(home),tree(appdata),tree(root))
    with pytest.raises(ValueError):updater.perform(root,'v0.1.7')
    assert len(calls)==2 and '--validate-only' in calls[1]
    assert (tree(home),tree(appdata),tree(root))==before


@pytest.mark.parametrize('mode,trailing,empty_reuse,expected_exit',[
    ('exit23',False,False,23),
    ('success',True,False,2),
    ('success',True,True,2),
    ('success',False,True,0),
],ids=['actual-child-exit23','unsupported-trailing-terminator','unsupported-terminator-after-empty-reuse','accepted-empty-reuse-without-terminator'])
def test_actual_synthetic_bootstrap_cli_preserves_child_exit_and_accepted_argv(tmp_path,monkeypatch,mode,trailing,empty_reuse,expected_exit):
    """Run real Python processes; the fixture never applies an installation."""
    import venv
    home,appdata,vault,root,_,_=context(tmp_path,monkeypatch)
    release=tmp_path/'synthetic verified CLI project';release.mkdir()
    venv.EnvBuilder(with_pip=False,symlinks=os.name!='nt').create(release/'.venv')
    selected=release/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
    assert selected.is_file()
    if os.name!='nt':assert selected.is_symlink()
    observation=tmp_path/'cli-events.jsonl'
    # This exact-source wrapper leaves main()/its SystemExit unguarded. The
    # child is a real isolated venv process, not a forged CompletedProcess.
    wrapper='''from pathlib import Path
import importlib.util,json,os,subprocess,sys
source=Path(SOURCE_LITERAL)
spec=importlib.util.spec_from_file_location('r133_actual_cli_installer',source/'install.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.REPO=Path(__file__).parent
home=Path(os.environ['R133_FIXTURE_HOME'])
Path.home=classmethod(lambda cls:home)
child=os.environ.get('R133_FIXTURE_CHILD')=='1'
def record(kind,**extra):
    with open(os.environ['R133_FIXTURE_OUTPUT'],'a',encoding='utf-8') as output:
        output.write(json.dumps({'kind':kind,'child':child,**extra})+'\\n')
def forbidden(*args,**kwargs):raise AssertionError('Fixture must not apply runtime/client/service changes.')
for name in ('atomic','private_dir','backup','clients'):setattr(module,name,forbidden)
real_run=subprocess.run
def run(args,**kwargs):
    if args[0]=='uv':
        assert not child,'Bootstrap must not loop after an empty original reuse value.'
        record('uv',argv=args)
        return subprocess.CompletedProcess(args,0)
    env=os.environ.copy();env['R133_FIXTURE_CHILD']='1'
    return real_run(args,env=env,**kwargs)
module.subprocess.run=run
def deploy(root,python,vault,role,configure):
    record('deploy-stub',argv=sys.argv,python=str(python),role=role,configure=configure)
    return {'status':'synthetic-no-installation'}
module.deploy=deploy
if child and os.environ['R133_FIXTURE_MODE']=='exit23':
    record('child-exit',argv=sys.argv,python=sys.executable)
    raise SystemExit(23)
module.main()
'''.replace('SOURCE_LITERAL',repr(str(SOURCE)))
    (release/'install.py').write_text(wrapper,encoding='utf-8',newline='\n')
    arguments=['--root',str(root),'--vault',str(vault),'--configure-clients']
    if empty_reuse:arguments.extend(['--reuse-python',''])
    if trailing:arguments.append('--')
    expected_arguments=list(arguments)
    insertion=expected_arguments.index('--') if '--' in expected_arguments else len(expected_arguments)
    expected_arguments[insertion:insertion]=['--reuse-python',str(selected)]
    temp=tmp_path/'process temp';temp.mkdir()
    env=os.environ.copy()
    env.update(HOME=str(home),USERPROFILE=str(home),APPDATA=str(appdata),XDG_CONFIG_HOME=str(home/'config'),
               TMP=str(temp),TEMP=str(temp),TMPDIR=str(temp),PYTHONDONTWRITEBYTECODE='1',
               R133_FIXTURE_HOME=str(home),R133_FIXTURE_OUTPUT=str(observation),R133_FIXTURE_MODE=mode)
    env.pop('R133_FIXTURE_CHILD',None)
    before=(tree(home),tree(appdata))
    process=subprocess.run([sys.executable,'-I',str(release/'install.py'),*arguments],env=env,
                           capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert process.returncode==expected_exit,process.stderr
    assert 'Traceback' not in process.stderr
    if expected_exit==2:
        # Executed a6 and v2 both reject this terminator before any handoff.
        assert 'unrecognized arguments: --' in process.stderr
        assert not observation.exists(),'Rejected arguments must not reach uv, a child or deploy.'
        assert (tree(home),tree(appdata))==before and not root.exists()
        return
    events=[json.loads(line) for line in observation.read_text(encoding='utf-8').splitlines()]
    assert len(events)==2 and events[0]['kind']=='uv' and events[0]['child'] is False
    assert events[1]['child'] is True and events[1]['argv'][1:]==expected_arguments
    assert events[1]['python']==str(selected)
    assert events[1]['kind']==('child-exit' if mode=='exit23' else 'deploy-stub')
    if mode=='success':assert events[1]['configure'] is True and events[1]['role']=='capture'
    assert (tree(home),tree(appdata))==before and not root.exists()
