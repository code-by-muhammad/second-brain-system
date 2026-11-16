"""The wiki is built by whichever assistant program is selected: claude or codex. No model is called here."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import pytest
import brain_wiki as w
import brain_settings as cfg

SOURCE=Path(__file__).resolve().parents[1]
posix=pytest.mark.skipif(os.name=='nt',reason='Non-Windows program layout and process group.')


def program(folder,name,body='#!/bin/sh\nexit 0\n'):
    folder.mkdir(parents=True,exist_ok=True);path=folder/name
    path.write_text(body,encoding='utf-8');path.chmod(path.stat().st_mode|stat.S_IXUSR)
    return path


def test_assistant_is_the_selected_program_name_and_nothing_else():
    suffix='.exe' if os.name=='nt' else ''
    assert w._model_provider('/tools/claude'+suffix)=='claude' and w._model_provider('/tools/codex'+suffix)=='codex'
    for other in ('/tools/claude.cmd','/tools/node','/tools/claude-code'+suffix,'/tools/python'+suffix):
        assert w._model_provider(other) is None


def test_both_commands_take_the_prompt_on_standard_input_with_no_tools_and_no_user_settings(tmp_path):
    schema=tmp_path/'schema.json';schema.write_text('{"type":"object"}',encoding='utf-8');output=tmp_path/'output.json'
    claude=w._model_command('claude',schema,output)
    assert claude[:3]==['-p','--output-format','json'] and claude[claude.index('--json-schema')+1]=='{"type":"object"}'
    assert claude[claude.index('--tools')+1]=='' and claude[claude.index('--setting-sources')+1]==''
    assert {'--strict-mcp-config','--disable-slash-commands','--no-session-persistence'}<=set(claude)
    codex=w._model_command('codex',schema,output)
    assert codex[0]=='exec' and codex[-1]=='-' and codex[codex.index('-o')+1]==str(output) and 'features.shell_tool=false' in codex


def test_claude_result_becomes_the_batch_output_and_a_refusal_is_reported(brain,tmp_path):
    reply=tmp_path/'reply.json';output=tmp_path/'output.json'
    reply.write_text(json.dumps({'is_error':False,'structured_output':{'pages':[]},'result':'ignored'}),encoding='utf-8')
    assert w._claude_output(reply,output) is None and json.loads(output.read_text(encoding='utf-8'))=={'pages':[]}
    output.unlink()
    for value,expected in (({'is_error':True,'result':'Invalid API key · Please run /login'},'Please run /login'),
                           ({'is_error':False,'result':'rate limit, 401 Unauthorized'},'no structured output'),({'is_error':False},'no structured output')):
        reply.write_text(json.dumps(value),encoding='utf-8')
        refused=w._claude_output(reply,output)
        assert expected in refused and not output.exists()
        # Text the model wrote is never what decides sign-in or usage attention.
        assert w._model_auth_quota_failure(1,'\n'+refused+'\n','packet','claude')==bool(value['is_error'])
    reply.write_text('not json',encoding='utf-8')
    assert w._claude_output(reply,output)=='unreadable result'
    assert w._model_auth_quota_failure(1,'\nInvalid API key · Please run /login\n','packet','claude')
    # A Codex run echoes note text into its log; Claude's wording in a note must not pause the worker.
    assert not w._model_auth_quota_failure(1,'echoed note: Invalid API key, Please run /login','another packet','codex')


@posix
def test_selection_follows_the_usual_install_link_and_records_the_file_it_names(brain,tmp_path):
    versions=tmp_path/'tools/versions';first=program(versions,'1.0.0');link=tmp_path/'tools/bin/claude'
    link.parent.mkdir();os.symlink(first,link)
    identity=w.configure_model(str(link))['executable']
    assert identity['provider']=='claude' and identity['path']==str(first.resolve())
    assert json.loads((cfg.STATE/'wiki-model.json').read_text())=={'format':1,'executable':str(link)}
    second=program(versions,'1.0.1','#!/bin/sh\nexit 1\n');link.unlink();os.symlink(second,link)  # The assistant updated itself.
    assert w._model_executable()['path']==str(second.resolve())
    with pytest.raises(ValueError,match='claude or codex'):w.configure_model(str(program(tmp_path/'tools','node')))
    with pytest.raises(ValueError,match='outside'):w.configure_model(str(program(cfg.VAULT,'codex')))


@posix
def test_program_runs_in_its_own_group_with_separate_reply_and_log(brain,tmp_path):
    script=program(tmp_path/'tools','synthetic-model','#!/bin/sh\ncat\necho diagnostics >&2\n')
    cwd=tmp_path/'work';cwd.mkdir();started=[]
    with (tmp_path/'runner.log').open('wb') as log,(tmp_path/'reply.json').open('wb') as reply:
        done=w._run_model_posix([str(script)],'Synthetic input',10,cwd=cwd,log=log,on_started=lambda:started.append(True),stdout=reply)
    assert done.returncode==0 and started==[True]
    assert (tmp_path/'reply.json').read_text()=='Synthetic input' and (tmp_path/'runner.log').read_text()=='diagnostics\n'


@posix
def test_deadline_ends_the_program_and_what_it_started(brain,tmp_path):
    import subprocess,time
    mark=tmp_path/'child.pid'
    script=program(tmp_path/'tools','synthetic-model','#!/bin/sh\nsleep 60 &\necho $! > "'+str(mark)+'"\nsleep 60\n')
    with (tmp_path/'runner.log').open('wb') as log:
        with pytest.raises(subprocess.TimeoutExpired):
            w._run_model_posix([str(script)],'',1,cwd=tmp_path,log=log,stdout=log,on_started=lambda:None)
    child=int(mark.read_text());deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        try:os.kill(child,0)
        except ProcessLookupError:break
        time.sleep(.1)
    else:pytest.fail('A helper the program started outlived the run.')


def setup_module_under_test():
    spec=importlib.util.spec_from_file_location('setup_device_choice',SOURCE/'setup_device.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_installed_capture_device_becomes_maintainer_only_when_the_notes_have_none(tmp_path,monkeypatch):
    setup=setup_module_under_test()
    root=tmp_path/'runtime';vault=tmp_path/'vault';(root/'state').mkdir(parents=True);vault.mkdir()
    device={'device_id':'abc123','role':'capture','version':'0.2.7','vault':str(vault),'python':sys.executable,'timezone':'UTC'}
    (root/'device.json').write_text(json.dumps(device),encoding='utf-8')
    calls=[]
    class Done:returncode=0;stderr='';stdout=''
    monkeypatch.setattr(setup,'brain',lambda device,*arguments,capture=False:calls.append(arguments) or Done())
    monkeypatch.setattr(setup.runpy,'run_path',lambda path:{'atomic':lambda target,text:Path(target).write_text(text,encoding='utf-8')})
    setup.make_maintainer({**device,'root':str(root)},root,vault,True)
    assert calls==[('service','stop','--wait-seconds','300')]
    assert json.loads((root/'device.json').read_text())=={**device,'role':'maintainer'}
    assert 'maintainer_device_id: abc123' in (vault/setup.CONTROL).read_text()
    (vault/setup.CONTROL).write_text('---\nmaintainer_device_id: other99\n---\n',encoding='utf-8')
    (root/'device.json').write_text(json.dumps(device),encoding='utf-8')
    with pytest.raises(SystemExit,match='another device'):setup.make_maintainer({**device,'root':str(root)},root,vault,True)
    assert json.loads((root/'device.json').read_text())['role']=='capture'
    # Another device claims the notes while this one is stopping its service: nothing is taken, the service comes back.
    (vault/setup.CONTROL).unlink();calls.clear()
    def racing(device,*arguments,capture=False):
        calls.append(arguments)
        if arguments[:2]==('service','stop'):(vault/setup.CONTROL).write_text('---\nmaintainer_device_id: other99\n---\n',encoding='utf-8')
        return Done()
    monkeypatch.setattr(setup,'brain',racing)
    with pytest.raises(SystemExit,match='another device'):setup.make_maintainer({**device,'root':str(root)},root,vault,True)
    assert json.loads((root/'device.json').read_text())['role']=='capture' and calls[-1]==('service','start')
    assert 'other99' in (vault/setup.CONTROL).read_text()
    # The device record cannot be written: the ownership note this run created is withdrawn again.
    (vault/setup.CONTROL).unlink();monkeypatch.setattr(setup,'brain',lambda device,*arguments,capture=False:Done())
    def refuse(target,text):raise OSError('disk full')
    monkeypatch.setattr(setup.runpy,'run_path',lambda path:{'atomic':refuse})
    with pytest.raises(OSError):setup.make_maintainer({**device,'root':str(root)},root,vault,True)
    assert not (vault/setup.CONTROL).exists() and json.loads((root/'device.json').read_text())['role']=='capture'
    # Another device replaces the note while the record write is failing: its note is not removed.
    def replaced_then_refused(target,text):
        (vault/setup.CONTROL).write_text('---\nmaintainer_device_id: other99\n---\n',encoding='utf-8');raise OSError('disk full')
    monkeypatch.setattr(setup.runpy,'run_path',lambda path:{'atomic':replaced_then_refused})
    with pytest.raises(OSError):setup.make_maintainer({**device,'root':str(root)},root,vault,True)
    assert 'other99' in (vault/setup.CONTROL).read_text() and json.loads((root/'device.json').read_text())['role']=='capture'


def test_codex_skill_already_in_the_common_folder_is_kept_current_and_not_doubled(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('choice_install',SOURCE/'install.py')
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer);helpers=installer.setup_helpers()
    home=tmp_path/'home';common=home/'.agents/skills/second-brain';common.mkdir(parents=True);(common/'SKILL.md').write_text('old skill\n')
    (home/'.codex').mkdir()
    monkeypatch.setattr(helpers,'desktop_busy',lambda:False)
    result=helpers.plan(tmp_path/'runtime',tmp_path/'python.exe','new protocol',installer._codex_toml_plan,home=home,skill='new skill\n',selected=['codex'])
    skills=[row for row in result['files'] if row['path'].endswith('SKILL.md')]
    assert [row['path'] for row in skills]==[str((common/'SKILL.md').resolve())] and skills[0]['after']=='new skill\n'
    helpers.validate_plan(result,tmp_path/'runtime')
    assert not (home/'.codex/skills').exists()


@posix
def test_program_that_ends_between_checks_is_still_held_to_the_output_bound(brain,tmp_path):
    script=program(tmp_path/'tools','synthetic-model','#!/bin/sh\nhead -c 6000000 /dev/zero\n')
    with (tmp_path/'runner.log').open('wb') as log,(tmp_path/'reply.json').open('wb') as reply:
        with pytest.raises(RuntimeError,match='byte budget'):
            w._run_model_posix([str(script)],'',10,cwd=tmp_path,log=log,stdout=reply,on_started=lambda:None)


@posix
@pytest.mark.parametrize('name',['SIGTERM','SIGHUP','SIGINT'])
def test_ending_the_runner_or_closing_its_terminal_ends_the_model_it_started(brain,tmp_path,name):
    import signal,subprocess,time
    ending=getattr(signal,name)
    mark=tmp_path/'model.pid'
    model=program(tmp_path/'tools','synthetic-model','#!/bin/sh\necho $$ > "'+str(mark)+'"\nsleep 60\n')
    runner=tmp_path/'runner.py'
    runner.write_text('import sys\nsys.path.insert(0,sys.argv[1])\nimport brain_wiki as w\n'
                      'with open(sys.argv[3],"wb") as log:\n    w._run_model_posix([sys.argv[2]],"",60,cwd=sys.argv[4],log=log,stdout=log,on_started=lambda:None)\n',encoding='utf-8')
    child=subprocess.Popen([sys.executable,str(runner),str(SOURCE/'app'),str(model),str(tmp_path/'runner.log'),str(tmp_path)],env=dict(os.environ))
    deadline=time.monotonic()+20
    while not mark.exists() or not mark.read_text().strip():
        assert time.monotonic()<deadline and child.poll() is None;time.sleep(.1)
    pid=int(mark.read_text());child.send_signal(ending);child.wait(timeout=20)
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        try:os.kill(pid,0)
        except ProcessLookupError:break
        time.sleep(.1)
    else:pytest.fail('The model outlived its terminated runner.')


def test_per_user_program_is_accepted_from_the_home_folder_and_refused_from_the_folder_that_holds_it(brain,tmp_path,monkeypatch):
    home=tmp_path/'home';tools=home/'.local/bin';tools.mkdir(parents=True)
    tool=tools/('claude.exe' if os.name=='nt' else 'claude');tool.write_bytes(b'Synthetic inert identity, never execute.');tool.chmod(0o700)
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    for folder in (home,home.parent):  # Where a new terminal opens, and a folder above it.
        monkeypatch.chdir(folder)
        assert w._native_model_identity(str(tool))['provider']=='claude'
    for folder in (tools,tools.parent):  # A project-like folder that itself contains the tool.
        monkeypatch.chdir(folder)
        with pytest.raises(ValueError,match='outside'):w._native_model_identity(str(tool))


def test_setup_offers_only_real_programs_from_absolute_path_folders(tmp_path,monkeypatch):
    setup=setup_module_under_test();suffix='.exe' if os.name=='nt' else ''
    first=tmp_path/'launchers';second=tmp_path/'native';third=tmp_path/'again'
    for folder in (first,second,third):folder.mkdir()
    (first/'claude.cmd').write_text('launcher');(first/'claude.ps1').write_text('launcher')
    real=second/('claude'+suffix);real.write_bytes(b'inert');real.chmod(0o700)
    planted=tmp_path/'cwd';planted.mkdir();(planted/('claude'+suffix)).write_bytes(b'inert');(planted/('claude'+suffix)).chmod(0o700)
    monkeypatch.chdir(planted)
    monkeypatch.setenv('PATH',os.pathsep.join(['','.',str(first),str(second),str(third),str(second)]))
    assert setup.wiki_programs('claude')==[str(real.resolve() if os.name=='nt' else real)]
    assert setup.wiki_programs('codex')==[]


def test_claude_is_started_with_file_attachments_switched_off(brain,monkeypatch):
    """Claude Code would read a file named as @path in a note; the model process must not."""
    from test_wiki import fixture
    source,packet,result=fixture(brain);seen={}
    monkeypatch.setattr(w,'_model_executable',lambda:{'path':'/tools/claude','sha256':'0'*64,'bytes':1,'provider':'claude'})
    def transport(command,model_input,timeout,**kwargs):
        seen.update(command=command,env=kwargs['env'],separate=kwargs['stdout'] is not kwargs['log'])
        kwargs['on_started']();raise RuntimeError('stop before any model output')
    monkeypatch.setattr(w,'_run_model',transport)
    with pytest.raises(RuntimeError,match='stop before'):w.run(limit=1)
    assert seen['env']['CLAUDE_CODE_DISABLE_ATTACHMENTS']=='1' and seen['env'].get('PATH')==os.environ.get('PATH')
    assert seen['command'][:2]==['/tools/claude','-p'] and seen['separate']


def test_finished_batch_cleanup_also_removes_the_saved_claude_reply(brain):
    from datetime import datetime,timedelta,timezone
    from test_wiki import fixture
    source,packet,result=fixture(brain);w.apply(packet,result)
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];(folder/'model-reply.json').write_text('{"result":"private"}',encoding='utf-8')
    old=(datetime.now(timezone.utc)-timedelta(days=40)).isoformat()
    with w.db() as conn:conn.execute('UPDATE batches SET updated=? WHERE id=?',(old,packet['batch_id']))
    assert w.cleanup()>0 and not (folder/'model-reply.json').exists()


def test_role_change_keeps_a_record_that_was_already_written_and_refuses_beside_a_running_service(tmp_path,monkeypatch):
    setup=setup_module_under_test()
    root=tmp_path/'runtime';vault=tmp_path/'vault';(root/'state').mkdir(parents=True);vault.mkdir()
    device={'device_id':'abc123','role':'capture','version':'0.2.7','vault':str(vault),'python':sys.executable,'timezone':'UTC'}
    (root/'device.json').write_text(json.dumps(device),encoding='utf-8')
    class Done:returncode=0;stderr='';stdout=''
    monkeypatch.setattr(setup,'brain',lambda device,*arguments,capture=False:Done())
    def interrupted_after_replacing(target,text):Path(target).write_text(text,encoding='utf-8');raise KeyboardInterrupt
    monkeypatch.setattr(setup.runpy,'run_path',lambda path:{'atomic':interrupted_after_replacing})
    with pytest.raises(KeyboardInterrupt):setup.make_maintainer({**device,'root':str(root)},root,vault,True)
    assert json.loads((root/'device.json').read_text())['role']=='maintainer' and 'abc123' in (vault/setup.CONTROL).read_text()
    # Without the service step, a server that is running would keep answering as a capture device.
    (root/'device.json').write_text(json.dumps(device),encoding='utf-8');(vault/setup.CONTROL).unlink()
    (root/'state/service-pids.json').write_text('{}',encoding='utf-8')  # The service has run here, whatever it answers now.
    with pytest.raises(SystemExit,match='has a background service'):setup.make_maintainer({**device,'root':str(root)},root,vault,False)
    assert json.loads((root/'device.json').read_text())['role']=='capture' and not (vault/setup.CONTROL).exists()


@posix
def test_codex_skill_link_that_leads_nowhere_does_not_become_a_second_copy(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('choice_install_link',SOURCE/'install.py')
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer);helpers=installer.setup_helpers()
    home=tmp_path/'home';common=home/'.agents/skills/second-brain';common.mkdir(parents=True);(common/'SKILL.md').write_text('old skill\n')
    own=home/'.codex/skills/second-brain';own.mkdir(parents=True);os.symlink(home/'gone/SKILL.md',own/'SKILL.md')
    monkeypatch.setattr(helpers,'desktop_busy',lambda:False)
    result=helpers.plan(tmp_path/'runtime',tmp_path/'python.exe','new protocol',installer._codex_toml_plan,home=home,skill='new skill\n',selected=['codex'])
    assert [row['path'] for row in result['files'] if row['path'].endswith('SKILL.md')]==[str((common/'SKILL.md').resolve())]
    assert (own/'SKILL.md').is_symlink() and not (home/'gone').exists()


def test_failed_stop_before_a_role_change_starts_the_service_again_and_says_so(tmp_path,monkeypatch):
    setup=setup_module_under_test()
    root=tmp_path/'runtime';vault=tmp_path/'vault';(root/'state').mkdir(parents=True);vault.mkdir()
    device={'device_id':'abc123','role':'capture','version':'0.2.7','vault':str(vault),'python':sys.executable,'timezone':'UTC'}
    (root/'device.json').write_text(json.dumps(device),encoding='utf-8');calls=[]
    class Result:
        def __init__(self,code):self.returncode=code;self.stderr='OwnershipError: Stop failed during native end; no empty-runtime receipt.';self.stdout=''
    def controller(device,*arguments,capture=False):
        calls.append(arguments[:2]);return Result(1 if arguments[:2]==('service','stop') else 0)
    monkeypatch.setattr(setup,'brain',controller)
    monkeypatch.setattr(setup.runpy,'run_path',lambda path:{'atomic':lambda target,text:pytest.fail('The record must not be written.')})
    with pytest.raises(SystemExit,match='role was not changed'):setup.make_maintainer({**device,'root':str(root)},root,vault,True)
    assert calls==[('service','stop'),('service','start')] and not (vault/setup.CONTROL).exists()
    # Ctrl+C while the stop is still waiting may already have ended the service: it is started again too.
    calls.clear()
    def interrupted(device,*arguments,capture=False):
        calls.append(arguments[:2])
        if arguments[:2]==('service','stop'):raise KeyboardInterrupt
        return Result(0)
    monkeypatch.setattr(setup,'brain',interrupted)
    with pytest.raises(KeyboardInterrupt):setup.make_maintainer({**device,'root':str(root)},root,vault,True)
    assert calls==[('service','stop'),('service','start')] and json.loads((root/'device.json').read_text())['role']=='capture'


@posix
def test_interrupt_that_arrives_while_the_model_is_being_started_still_ends_it(brain,tmp_path,monkeypatch):
    import signal,subprocess,time
    mark=tmp_path/'model.pid'
    script=program(tmp_path/'tools','synthetic-model','#!/bin/sh\necho $$ > "'+str(mark)+'"\nsleep 60\n')
    actual=subprocess.Popen
    def launch_then_interrupt(*args,**kwargs):
        process=actual(*args,**kwargs);os.kill(os.getpid(),signal.SIGINT);time.sleep(.2)  # Before the caller holds the handle.
        return process
    monkeypatch.setattr(w.subprocess,'Popen',launch_then_interrupt)
    with (tmp_path/'runner.log').open('wb') as log:
        with pytest.raises(KeyboardInterrupt):
            w._run_model_posix([str(script)],'',60,cwd=tmp_path,log=log,stdout=log,on_started=lambda:None)
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    if mark.exists() and mark.read_text().strip():
        with pytest.raises(ProcessLookupError):os.kill(int(mark.read_text()),0)
