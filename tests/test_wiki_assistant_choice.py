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
    assert claude[:7]==['-p','--model','claude-opus-5-5','--effort','high','--output-format','json'] and claude[claude.index('--json-schema')+1]=='{"type":"object"}'
    assert claude[claude.index('--tools')+1]=='' and claude[claude.index('--setting-sources')+1]==''
    assert {'--strict-mcp-config','--disable-slash-commands','--no-session-persistence'}<=set(claude)
    codex=w._model_command('codex',schema,output)
    assert codex[:5]==['exec','-m','gpt-6.1-sol','-c','model_reasoning_effort="high"'] and codex[-1]=='-' and codex[codex.index('-o')+1]==str(output) and {'features.shell_tool=false','features.hooks=false','features.view_image=false','features.computer_use=false','features.code_mode=false','features.code_mode_host=false','features.sleep_tool=false','features.goals=false','hide_agent_reasoning=true'}<=set(codex)


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


def writer(device_write):
    """The installer's atomic writer in miniature: a new file is created whole and never over an existing one."""
    def atomic(target,text,expected_sha256='observe'):
        if expected_sha256 is not None:return device_write(target,text)
        with Path(target).open('x',encoding='utf-8') as output:output.write(text)
    return atomic


def setup_module_under_test():
    spec=importlib.util.spec_from_file_location('setup_device_choice',SOURCE/'setup_device.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_installed_capture_device_becomes_maintainer_only_when_the_notes_have_none(tmp_path,monkeypatch):
    setup=setup_module_under_test()
    root=tmp_path/'runtime';vault=tmp_path/'vault';(root/'state').mkdir(parents=True);vault.mkdir();(root/'state/service-pids.json').write_text('{}')
    device={'device_id':'abc123','role':'capture','version':'0.2.9','vault':str(vault),'python':sys.executable,'timezone':'UTC'}
    (root/'device.json').write_text(json.dumps(device),encoding='utf-8')
    calls=[]
    class Done:returncode=0;stderr='';stdout=''
    monkeypatch.setattr(setup,'brain',lambda device,*arguments,capture=False:calls.append(arguments) or Done())
    monkeypatch.setattr(setup.runpy,'run_path',lambda path:{'atomic':writer(lambda target,text:Path(target).write_text(text,encoding='utf-8'))})
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
    monkeypatch.setattr(setup.runpy,'run_path',lambda path:{'atomic':writer(refuse)})
    with pytest.raises(OSError):setup.make_maintainer({**device,'root':str(root)},root,vault,True)
    assert not (vault/setup.CONTROL).exists() and json.loads((root/'device.json').read_text())['role']=='capture'
    # Another device replaces the note while the record write is failing: its note is not removed.
    def replaced_then_refused(target,text):
        (vault/setup.CONTROL).write_text('---\nmaintainer_device_id: other99\n---\n',encoding='utf-8');raise OSError('disk full')
    monkeypatch.setattr(setup.runpy,'run_path',lambda path:{'atomic':writer(replaced_then_refused)})
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
    for name,value in (('CLAUDE_CODE_PLUGIN_DIRS','/planted/plugins'),('CLAUDECODE','1'),('CLAUDE_CODE_ENTRYPOINT','cli'),('CLAUDE_CONFIG_DIR','/kept/sign-in')):monkeypatch.setenv(name,value)
    with pytest.raises(RuntimeError,match='stop before'):w.run(limit=1)
    assert seen['env']['CLAUDE_CODE_DISABLE_ATTACHMENTS']=='1' and seen['env'].get('PATH')==os.environ.get('PATH')
    assert not {'CLAUDE_CODE_PLUGIN_DIRS','CLAUDECODE','CLAUDE_CODE_ENTRYPOINT'}&set(seen['env']) and seen['env']['CLAUDE_CONFIG_DIR']=='/kept/sign-in'
    assert seen['command'][seen['command'].index('--settings')+1]=='{"disableAllHooks":true}'
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
    root=tmp_path/'runtime';vault=tmp_path/'vault';(root/'state').mkdir(parents=True);vault.mkdir();(root/'state/service-pids.json').write_text('{}')
    device={'device_id':'abc123','role':'capture','version':'0.2.9','vault':str(vault),'python':sys.executable,'timezone':'UTC'}
    (root/'device.json').write_text(json.dumps(device),encoding='utf-8')
    class Done:returncode=0;stderr='';stdout=''
    monkeypatch.setattr(setup,'brain',lambda device,*arguments,capture=False:Done())
    def interrupted_after_replacing(target,text):Path(target).write_text(text,encoding='utf-8');raise KeyboardInterrupt
    monkeypatch.setattr(setup.runpy,'run_path',lambda path:{'atomic':writer(interrupted_after_replacing)})
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
    root=tmp_path/'runtime';vault=tmp_path/'vault';(root/'state').mkdir(parents=True);vault.mkdir();(root/'state/service-pids.json').write_text('{}')
    device={'device_id':'abc123','role':'capture','version':'0.2.9','vault':str(vault),'python':sys.executable,'timezone':'UTC'}
    (root/'device.json').write_text(json.dumps(device),encoding='utf-8');calls=[]
    class Result:
        def __init__(self,code):self.returncode=code;self.stderr='OwnershipError: Stop failed during native end; no empty-runtime receipt.';self.stdout=''
    def controller(device,*arguments,capture=False):
        calls.append(arguments[:2]);return Result(1 if arguments[:2]==('service','stop') else 0)
    monkeypatch.setattr(setup,'brain',controller)
    monkeypatch.setattr(setup.runpy,'run_path',lambda path:{'atomic':writer(lambda target,text:pytest.fail('The record must not be written.'))})
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


@posix
def test_role_change_refuses_a_linked_ownership_note_and_undoes_a_note_written_just_before_an_interrupt(tmp_path,monkeypatch):
    setup=setup_module_under_test()
    root=tmp_path/'runtime';vault=tmp_path/'vault';(root/'state').mkdir(parents=True);vault.mkdir();(root/'state/service-pids.json').write_text('{}')
    device={'device_id':'abc123','role':'capture','version':'0.2.9','vault':str(vault),'python':sys.executable,'timezone':'UTC'}
    (root/'device.json').write_text(json.dumps(device),encoding='utf-8')
    class Done:returncode=0;stderr='';stdout=''
    monkeypatch.setattr(setup,'brain',lambda device,*arguments,capture=False:Done())
    monkeypatch.setattr(setup.runpy,'run_path',lambda path:{'atomic':writer(lambda target,text:pytest.fail('The record must not be written.'))})
    actual=setup.own_the_notes
    def interrupted_after_writing(vault,device_id):actual(vault,device_id);raise KeyboardInterrupt
    monkeypatch.setattr(setup,'own_the_notes',interrupted_after_writing)
    with pytest.raises(KeyboardInterrupt):setup.make_maintainer({**device,'root':str(root)},root,vault,True)
    assert not (vault/setup.CONTROL).exists() and json.loads((root/'device.json').read_text())['role']=='capture'
    # A note that is a link is refused before anything stops, even when it names this device.
    elsewhere=tmp_path/'elsewhere.md';elsewhere.write_text('---\nmaintainer_device_id: abc123\n---\n',encoding='utf-8')
    os.symlink(elsewhere,vault/setup.CONTROL)
    with pytest.raises(SystemExit,match='linked'):setup.make_maintainer({**device,'root':str(root)},root,vault,True)


def test_codex_log_is_classified_by_position_so_neither_note_nor_model_text_decides():
    quoted='ERROR: unexpected status 401 Unauthorized'
    packet='Compile sources\nINPUT_JSON:\n{"source":"'+'ordinary text '*6000+' '+quoted+' and ERROR: rate limit are quoted in this note"}'
    header='OpenAI Codex\n--------\nuser\n'
    cut=(header+packet).encode('utf-8')[:65536].decode('utf-8',errors='replace')  # What the worker reads of a long echo.
    assert not w._model_auth_quota_failure(1,cut,packet,'codex')
    small='Compile sources\nINPUT_JSON:\n{"source":"a note that quotes '+quoted+'"}'
    # The model's own text after the `codex` line is never a diagnostic.
    assert not w._model_auth_quota_failure(1,header+small+'\ncodex\nERROR: rate limit\n{"pages":[]}\ntokens used',small,'codex')
    # A real diagnostic counts, also when the note quotes the very same line.
    for line in (quoted,'2026-11-16T09:53:37.187645Z ERROR codex_core: usage limit reached'):
        assert w._model_auth_quota_failure(1,header+small+'\n'+line,small,'codex')
    assert w._model_auth_quota_failure(1,header+small+'\n'+quoted+'\ncodex\n{"pages":[]}',small,'codex')
    # A log of another shape decides nothing.
    assert not w._model_auth_quota_failure(1,small+'\n'+quoted,small,'codex')


def test_both_programs_start_without_node_preloads(monkeypatch):
    inherited={'PATH':'/bin','NODE_OPTIONS':'--require /planted.js','node_options':'--require /planted.js','CLAUDE_CODE_PLUGIN_DIRS':'/p','HOME':'/h'}
    assert w._model_environment('codex',inherited)=={'PATH':'/bin','CLAUDE_CODE_PLUGIN_DIRS':'/p','HOME':'/h'}
    assert w._model_environment('claude',inherited)=={'PATH':'/bin','HOME':'/h','CLAUDE_CODE_DISABLE_ATTACHMENTS':'1'}


@posix
def test_request_to_stop_that_arrives_during_cleanup_is_not_lost(brain,tmp_path,monkeypatch):
    import signal
    script=program(tmp_path/'tools','synthetic-model','#!/bin/sh\nexit 0\n')
    actual=os.killpg
    def interrupt_during_cleanup(group,number):
        if number==signal.SIGKILL:os.kill(os.getpid(),signal.SIGINT)
        return actual(group,number)
    monkeypatch.setattr(w.os,'killpg',interrupt_during_cleanup)
    with (tmp_path/'runner.log').open('wb') as log:
        with pytest.raises(KeyboardInterrupt):
            w._run_model_posix([str(script)],'',10,cwd=tmp_path,log=log,stdout=log,on_started=lambda:None)


def test_setup_reports_why_an_existing_registration_did_not_start_and_registers_only_a_missing_one(monkeypatch):
    setup=setup_module_under_test();calls=[]
    class Result:
        def __init__(self,code,text=''):self.returncode=code;self.stderr=text;self.stdout=''
    def refused(device,*arguments,capture=False):
        calls.append(arguments[1]);return Result(1,'OwnershipError: Startup readiness failed (OwnershipError: no heartbeat)')
    monkeypatch.setattr(setup,'brain',refused)
    with pytest.raises(SystemExit,match='could not start: OwnershipError: Startup readiness failed'):setup.start_service({},'0.2.9',Path('/r'),registered=True)
    assert calls==['start']
    calls.clear();answers=iter([Result(1,'OwnershipError: The owned Mac registration is not installed.'),Result(0),Result(0)])
    monkeypatch.setattr(setup,'brain',lambda device,*arguments,capture=False:calls.append(arguments[1]) or next(answers))
    monkeypatch.setattr(setup,'wait_running',lambda *args,**kwargs:True)
    setup.start_service({},'0.2.9',Path('/r'),registered=True)
    assert calls==['start','install','start']


@pytest.mark.skipif(os.name!='nt',reason='Only Windows names a linked program by its target.')
def test_windows_link_to_a_program_with_another_name_is_not_offered(tmp_path,monkeypatch):
    setup=setup_module_under_test();folder=tmp_path/'links';folder.mkdir()
    (folder/'claude.exe').write_bytes(b'inert');(folder/'claude.exe').chmod(0o700)
    monkeypatch.setenv('PATH',str(folder))
    actual=Path.resolve
    monkeypatch.setattr(Path,'resolve',lambda self,*args,**kwargs:self.with_name('codex.exe') if self.name=='claude.exe' else actual(self,*args,**kwargs))
    assert setup.wiki_programs('claude')==[]


def test_role_change_reads_the_owner_as_the_service_does_and_skips_the_stop_of_an_unregistered_service(tmp_path,monkeypatch):
    setup=setup_module_under_test()
    root=tmp_path/'runtime';vault=tmp_path/'vault';(root/'state').mkdir(parents=True);vault.mkdir()
    device={'device_id':'abc123','role':'capture','version':'0.2.9','vault':str(vault),'python':sys.executable,'timezone':'UTC'}
    (root/'device.json').write_text(json.dumps(device),encoding='utf-8');calls=[]
    class Result:
        def __init__(self,code,text=''):self.returncode=code;self.stderr=text;self.stdout=''
    def controller(device,*arguments,capture=False):
        calls.append(arguments[:2]);return Result(1,'OwnershipError: Mac native definition is missing or incomplete; no activation change attempted.')
    monkeypatch.setattr(setup,'brain',controller)
    monkeypatch.setattr(setup.runpy,'run_path',lambda path:{'atomic':writer(lambda target,text:Path(target).write_text(text,encoding='utf-8'))})
    # A line the service would not accept as this device's is not accepted here either.
    (vault/setup.CONTROL).parent.mkdir(parents=True);(vault/setup.CONTROL).write_text('---\nmaintainer_device_id: abc123  \n---\n',encoding='utf-8')
    with pytest.raises(SystemExit,match='another device'):setup.make_maintainer({**device,'root':str(root)},root,vault,True)
    assert calls==[] and json.loads((root/'device.json').read_text())['role']=='capture'
    (vault/setup.CONTROL).write_text('---\nmaintainer_device_id: abc123\n---\n'+'x'*2_000_000,encoding='utf-8')  # Too large for the service.
    with pytest.raises(SystemExit,match='another device'):setup.make_maintainer({**device,'root':str(root)},root,vault,True)
    (vault/setup.CONTROL).unlink()
    setup.make_maintainer({**device,'root':str(root)},root,vault,True)  # No service state files: nothing to stop.
    assert calls==[] and json.loads((root/'device.json').read_text())['role']=='maintainer'


def test_each_assistant_has_one_fixed_model_and_effort_unless_the_owner_selects_another(brain,tmp_path):
    assert w._model_choice('claude')==('claude-opus-5-5','high') and w._model_choice('codex')==('gpt-6.1-sol','high')
    schema=tmp_path/'schema.json';schema.write_text('{}',encoding='utf-8')
    claude=w._model_command('claude',schema,tmp_path/'o.json','claude-sonnet-5-5','medium')
    assert claude[claude.index('--model')+1]=='claude-sonnet-5-5' and claude[claude.index('--effort')+1]=='medium'
    codex=w._model_command('codex',schema,tmp_path/'o.json',None,'low')
    assert codex[codex.index('-m')+1]=='gpt-6.1-sol' and 'model_reasoning_effort="low"' in codex
    # A name or level that could carry anything else onto the command line is refused.
    for model in ('','x'*81,'opus --dangerously-skip-permissions','a"b','-m'):
        with pytest.raises(ValueError,match='Model name'):w._model_choice('claude',model)
    for provider,effort in (('claude','none'),('codex','max'),('codex','high" -c x="'),('claude','HIGH')):
        with pytest.raises(ValueError,match='Effort'):w._model_choice(provider,effort=effort)


@posix
def test_only_an_explicit_choice_is_stored_and_an_invalid_stored_one_calls_no_model(brain,tmp_path):
    tool=program(tmp_path/'tools','claude')
    identity=w.configure_model(str(tool))['executable']
    assert (identity['model'],identity['effort'])==('claude-opus-5-5','high')
    assert json.loads((cfg.STATE/'wiki-model.json').read_text())=={'format':1,'executable':str(tool)}
    identity=w.configure_model(str(tool),'claude-sonnet-5-5','medium')['executable']
    assert json.loads((cfg.STATE/'wiki-model.json').read_text())=={'format':1,'executable':str(tool),'model':'claude-sonnet-5-5','effort':'medium'}
    assert (w._model_executable()['model'],w._model_executable()['effort'])==('claude-sonnet-5-5','medium')
    # A choice that was not asked for by name leaves a record that is already there exactly as it is.
    kept=(cfg.STATE/'wiki-model.json').read_bytes()
    assert w.configure_model(str(tool),keep_existing=True)['status'].startswith('kept') and (cfg.STATE/'wiki-model.json').read_bytes()==kept
    with pytest.raises(ValueError,match='Effort'):w.configure_model(str(tool),effort='none')
    assert json.loads((cfg.STATE/'wiki-model.json').read_text())['effort']=='medium'  # A refused choice changes nothing.
    (cfg.STATE/'wiki-model.json').write_text(json.dumps({'format':1,'executable':str(tool),'model':'x y'}),encoding='utf-8')
    with pytest.raises(ValueError,match='Model name'):w._model_executable()
    for damaged in ({'model':None},{'effort':None},{'model':7}):
        (cfg.STATE/'wiki-model.json').write_text(json.dumps({'format':1,'executable':str(tool),**damaged}),encoding='utf-8')
        with pytest.raises(ValueError,match='Invalid model configuration'):w._model_executable()
    (cfg.STATE/'wiki-model.json').write_text(json.dumps({'format':1,'executable':str(tool),'other':1}),encoding='utf-8')
    with pytest.raises(ValueError,match='Invalid model configuration'):w._model_executable()


def test_supervisor_starts_the_pass_with_only_its_error_output_kept_and_never_touches_an_old_log(brain,monkeypatch):
    """One real supervisor cycle with a recording process."""
    import brain_supervisor as supervisor
    from supervisor_fixture import install
    install(monkeypatch,supervisor)
    logs=cfg.ROOT/'logs';logs.mkdir(parents=True,exist_ok=True)
    (cfg.STATE/'index-bootstrap-status.json').write_text('{"status":"verified"}',encoding='utf-8')
    old=logs/'service-maintenance.log';old.write_bytes(b'x'*5_000_001)
    started=[]
    class End(BaseException):pass
    class Process:
        def __init__(self,command,**kwargs):
            self.pid=4321;self.returncode=None
            if Path(command[1]).name=='brain_maintenance.py':
                started.append((Path(command[1]).name,kwargs['stdout'],kwargs['stderr'].name))
        def poll(self):return self.returncode
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def connect_ex(self,address):return 0
    def stop(seconds):raise End
    monkeypatch.setattr(supervisor.socket,'socket',Socket)
    monkeypatch.setattr(supervisor.subprocess,'Popen',Process)
    monkeypatch.setattr(supervisor.time,'monotonic',lambda:100000)
    monkeypatch.setattr(supervisor.time,'sleep',stop)
    monkeypatch.setattr(cfg,'environment',lambda:{});monkeypatch.setattr(cfg,'child_options',lambda:{})
    with pytest.raises(End):supervisor.main({})
    assert started==[('brain_maintenance.py',supervisor.subprocess.DEVNULL,str(logs/'service-upkeep.log'))]
    assert old.stat().st_size==5_000_001
    assert {path.name for path in logs.iterdir()}=={'service-bootstrap.log','service-maintenance.log','service-upkeep.log'}


def test_retired_maintenance_log_no_longer_keeps_a_device_in_attention_but_any_other_large_log_does(brain,monkeypatch):
    import brain_maintenance as maintenance
    monkeypatch.setattr(cfg,'ROLE','capture')
    logs=cfg.ROOT/'logs';logs.mkdir(parents=True,exist_ok=True)
    old=logs/'service-maintenance.log';old.write_bytes(b'x'*5_000_001)
    status=maintenance.maintenance(False)
    assert status['status']=='completed' and 'error' not in status and old.stat().st_size==5_000_001
    (logs/'service-upkeep.log').write_bytes(b'y'*5_000_001)
    assert maintenance.maintenance(False)['error'].startswith('Operational log rotation is deferred')


def test_pass_that_needs_attention_leaves_one_complete_bounded_line_for_the_log(brain,monkeypatch,capfdbinary):
    import runpy
    import brain_maintenance as maintenance
    reason='possible credential; redact before syncing'
    status={'at':'2026-11-16T20:00:00+05:00','status':'needs attention','stage':'finished','captures_linked':0,
            'git':'needs attention: 01-Projects/'+'né"\\'*100+'.md: '+reason,'error':'e'*5000,'review':[{'path':'a.md','reasons':['r']}]*500}
    record=json.loads(maintenance._attention_line(status))  # Always one complete record, whatever the lengths.
    assert set(record)=={'at','status','stage','error','git'} and record['error']=='e'*400 and record['status']=='needs attention'
    # The reason of a Git failure stands after the path; a long path with characters that need escaping must not cut it away.
    assert len(status['git'])>400 and record['git']==status['git'] and record['git'].endswith(reason)
    assert len(json.loads(maintenance._attention_line({**status,'git':'g'*9000}))['git'])==2000
    # Run by hand, the command prints its whole result as before; the short line goes to error output only.
    monkeypatch.setattr(sys,'argv',[str(Path(maintenance.__file__)),'--no-sync'])
    (cfg.ROOT/'logs').mkdir(parents=True,exist_ok=True);(cfg.ROOT/'logs/service-server.log').write_bytes(b'x'*5_000_001)
    runpy.run_path(maintenance.__file__,run_name='__main__')
    out,err=capfdbinary.readouterr()
    assert json.loads(out)['status']=='needs attention' and 'review' in json.loads(out)
    assert json.loads(err)['error'].startswith('Operational log rotation is deferred') and b'review' not in err


def test_maintainer_without_a_wiki_assistant_gets_the_only_one_installed_and_is_told_when_setup_cannot_decide(tmp_path,monkeypatch,capsys):
    setup=setup_module_under_test();suffix='.exe' if os.name=='nt' else '';calls=[]
    class Done:returncode=0;stderr='';stdout=''
    monkeypatch.setattr(setup,'brain',lambda device,*arguments,capture=False:calls.append(arguments) or Done())
    # How this run's command ends once --wiki codex is added, in the quoting of the shell the guide uses here.
    tail="'--yes' '--wiki' 'codex'" if os.name=='nt' else '--yes --wiki codex'
    both=tmp_path/'both';one=tmp_path/'one';none=tmp_path/'none'
    for folder,names in ((both,('claude','codex')),(one,('codex',)),(none,())):
        folder.mkdir()
        for name in names:(folder/(name+suffix)).write_bytes(b'inert');(folder/(name+suffix)).chmod(0o700)
    (none/'codex.cmd').write_text('launcher')
    only=str((one/('codex'+suffix)).resolve() if os.name=='nt' else one/('codex'+suffix))
    # One assistant: recorded without a question, and the run says which and that nothing is built yet.
    monkeypatch.setenv('PATH',str(one))
    assert setup.offer_wiki({},True)==''
    assert calls==[('wiki','configure-model','--executable',only,'--keep-existing')]
    said=capsys.readouterr().out
    assert 'codex is the only assistant program found' in said and 'Wiki assistant selected: '+only in said and 'No page is built' in said
    # Two assistants and nobody to ask: nothing is recorded and the exact next command is named.
    calls.clear();monkeypatch.setenv('PATH',str(both))
    monkeypatch.setattr(sys,'argv',['setup_device.py','--root','/r t','--local-only','--yes'])
    for assume in (True,False):
        said=setup.offer_wiki({},assume)
        assert '--wiki claude or --wiki codex' in said and said.count(str(SOURCE/'setup_device.py'))==2
        if os.name!='nt':assert said.endswith("--root '/r t' --local-only --yes --wiki codex") and ' -I -B ' in said
    assert calls==[]
    # Two assistants and a person at the terminal: the answer decides; anything else selects nothing.
    monkeypatch.setattr(setup.sys.stdin,'isatty',lambda:True,raising=False)
    monkeypatch.setattr('builtins.input',lambda prompt:' Claude ')
    assert setup.offer_wiki({},False)=='' and calls[0][-2].endswith('claude'+suffix) and calls[0][-1]=='--keep-existing' and len(calls)==1
    calls.clear();monkeypatch.setattr('builtins.input',lambda prompt:'')
    assert 'both installed' in setup.offer_wiki({},False) and calls==[]
    # Input that is closed at the question is no answer either; the run goes on.
    def closed(prompt):raise EOFError
    monkeypatch.setattr('builtins.input',closed)
    assert 'both installed' in setup.offer_wiki({},False) and calls==[]
    # None, with only a launcher present: named as missing, nothing recorded.
    monkeypatch.setenv('PATH',str(none))
    assert 'neither claude nor codex was found' in setup.offer_wiki({},True) and calls==[] and setup.offer_wiki({},True).rstrip().endswith(tail)
    # The only assistant is refused by the runtime: its reason is passed on.
    # A choice recorded by someone else while the question was open is kept, and the run says so.
    class Kept:returncode=0;stderr='';stdout='{\n  "status": "kept; a wiki assistant is already recorded and nothing was changed"\n}'
    monkeypatch.setattr(setup,'brain',lambda device,*arguments,capture=False:Kept())
    monkeypatch.setenv('PATH',str(one));capsys.readouterr()
    assert setup.offer_wiki({},True)=='' and 'left as it is' in capsys.readouterr().out
    class Refused:returncode=1;stderr='ValueError: Model executable must be outside runtime';stdout=''
    monkeypatch.setattr(setup,'brain',lambda device,*arguments,capture=False:Refused())
    monkeypatch.setenv('PATH',str(one))
    said=setup.offer_wiki({},True)
    assert 'was not selected' in said and 'must be outside runtime' in said and said.rstrip().endswith(tail)


def test_check_names_the_wiki_assistant_of_a_maintainer_and_warns_when_none_is_recorded_or_a_run_stopped(tmp_path):
    """The acceptance check is run as a program against a runtime whose answers are fixed."""
    import subprocess
    root=tmp_path/'runtime';vault=tmp_path/'vault';(root/'app').mkdir(parents=True);(root/'state').mkdir();vault.mkdir()
    health={'status':'running','version':'0.2.10','vault_exists':True,'database_exists':True,'maintenance':{'status':'completed','git':'synced'},
            'wiki':{'worker':{'status':'idle','attention_required':False}}}
    def runtime(model):
        # The model answer is what "wiki model" prints, or the error line it ends with.
        (root/'app/brain_cli.py').write_text('import json,sys\nhealth='+repr(health)+'\nmodel='+repr(model)+'\n'
            'if sys.argv[1]=="wiki":\n    assert sys.argv[2:]==["model"]\n    if isinstance(model,str):sys.exit("Traceback (most recent call last):\\n  File x\\n"+model)\n    print(json.dumps({"status":"selected","executable":model,"worker":health["wiki"]["worker"]}))\n'
            'else:print(json.dumps(health if sys.argv[1]=="health" else {"status":"verified"}))\n',encoding='utf-8')
    def device(role):(root/'device.json').write_text(json.dumps({'version':'0.2.10','role':role,'python':sys.executable,'vault':str(vault)}),encoding='utf-8')
    def line(*flags):
        done=subprocess.run([sys.executable,'-I','-B',str(SOURCE/'check_setup.py'),'--root',str(root),'--home',str(tmp_path/'home'),*flags],capture_output=True,text=True,timeout=120)
        assert done.returncode==0,done.stdout+done.stderr
        return [row for row in done.stdout.splitlines() if 'wiki assistant' in row]
    chosen={'path':'/opt/tools/codex','provider':'codex','model':'gpt-6.1-sol','effort':'high'}
    runtime(chosen);device('capture')
    assert line()==[]
    device('maintainer')
    # No record: the runtime is not even asked, and the line names the command.
    assert len(line())==1 and line()[0].startswith('WARN') and 'none selected' in line()[0] and '--wiki claude or --wiki codex' in line()[0]
    (root/'state/wiki-model.json').write_text('{}',encoding='utf-8')
    assert line()==['PASS   wiki assistant: /opt/tools/codex, model gpt-6.1-sol, effort high']
    health['wiki']['worker']={'status':'attention','attention_required':True,'reason':'RuntimeError'}
    runtime(chosen)
    assert line()[0].startswith('WARN') and '/opt/tools/codex' in line()[0] and 'needs attention (RuntimeError)' in line()[0] and 'wiki retry' in line()[0]
    # The stopped run is saved state, so a device without the service shows the same line.
    assert line('--no-service')==line()
    # A record the runtime refuses is never a PASS, whatever the file looks like, and its reason is shown.
    health['wiki']['worker']={'status':'idle','attention_required':False}
    runtime('ValueError: Invalid model configuration; no model called.')
    assert line()[0].startswith('WARN') and 'cannot be used (ValueError: Invalid model configuration; no model called.)' in line()[0] and '--wiki' in line()[0]
    for odd in ({},[],{'path':''},None):
        runtime(odd)
        assert line()[0].startswith('WARN') and 'cannot be used' in line()[0]


@posix
def test_wiki_model_command_reads_the_record_as_a_run_does_and_starts_nothing(brain,tmp_path,monkeypatch,capsys):
    import brain_cli
    started=[]
    monkeypatch.setattr(w.subprocess,'Popen',lambda *args,**kwargs:started.append(args))
    monkeypatch.setattr(sys,'argv',['brain_cli.py','wiki','model'])
    with pytest.raises(RuntimeError,match='Configure the claude or codex program'):brain_cli.main()
    tool=program(tmp_path/'tools','codex')
    w.configure_model(str(tool));capsys.readouterr()
    brain_cli.main()
    answer=json.loads(capsys.readouterr().out)
    assert answer['status']=='selected' and answer['executable']['path']==str(tool.resolve()) and (answer['executable']['model'],answer['executable']['effort'])==('gpt-6.1-sol','high')
    assert answer['worker']==w._worker_summary() and answer['worker']['attention_required'] is False
    for damaged in ({'format':2,'executable':str(tool)},{'format':1,'executable':str(tool),'model':None},{'format':1,'executable':str(tmp_path/'gone/codex')}):
        (cfg.STATE/'wiki-model.json').write_text(json.dumps(damaged),encoding='utf-8')
        with pytest.raises(ValueError):brain_cli.main()
    # A program that can no longer be started is refused here and at selection, not only when a run reaches it.
    w.configure_model(str(tool));tool.chmod(0o600)
    with pytest.raises(ValueError,match='not executable'):brain_cli.main()
    with pytest.raises(ValueError,match='not executable'):w.configure_model(str(tool))
    assert started==[]


def test_setup_offers_a_wiki_assistant_only_to_a_maintainer_that_has_none_and_repeats_what_is_open_last(tmp_path,monkeypatch,capsys):
    setup=setup_module_under_test()
    root=tmp_path/'runtime';vault=tmp_path/'vault';(root/'state').mkdir(parents=True);vault.mkdir()
    def device(role):(root/'device.json').write_text(json.dumps({'device_id':'abc123','role':role,'version':'0.2.10','vault':str(vault),'python':sys.executable}),encoding='utf-8')
    offered=[];selected=[]
    class Done:returncode=0;stderr='';stdout=''
    monkeypatch.setattr(setup.runpy,'run_path',lambda path:{'verify':lambda repo:{'version':'0.2.10'}})
    monkeypatch.setattr(setup,'run',lambda command,capture=False:Done())
    monkeypatch.setattr(setup,'wait_running',lambda *args,**kwargs:True)
    monkeypatch.setattr(setup,'offer_wiki',lambda device,assume:offered.append(assume) or 'No wiki assistant is selected: both.')
    monkeypatch.setattr(setup,'select_wiki',lambda device,name:selected.append(name))
    def go(*flags):
        monkeypatch.setattr(sys,'argv',['setup_device.py','--root',str(root),'--clients','',*flags])
        with pytest.raises(SystemExit) as stop:setup.main()
        return stop.value.code,capsys.readouterr().out
    device('maintainer')
    code,said=go('--yes')
    assert code==0 and offered==[True] and said.rstrip().endswith('Still open: No wiki assistant is selected: both.')
    assert said.index('No wiki assistant')<said.index('Acceptance check')
    offered.clear()
    assert go('--check')[0]==0 and go('--wiki','codex')[0]==0 and offered==[] and selected==['codex']
    (root/'state/wiki-model.json').write_text('{}',encoding='utf-8')
    assert 'Still open' not in go()[1] and offered==[]
    (root/'state/wiki-model.json').unlink();device('capture')
    assert 'Still open' not in go()[1] and offered==[]


def test_setup_and_check_print_a_path_the_console_encoding_lacks_instead_of_stopping(tmp_path,monkeypatch):
    """A redirected Windows console is often cp1252; an assistant under a user folder with other letters must still be shown."""
    import io
    root=tmp_path/'runtime';vault=tmp_path/'vault';(root/'app').mkdir(parents=True);(root/'state').mkdir();vault.mkdir()
    path='/Users/\u674e/tools/codex'
    (root/'device.json').write_text(json.dumps({'device_id':'abc123','version':'0.2.10','role':'maintainer','python':sys.executable,'vault':str(vault)}),encoding='utf-8')
    (root/'state/wiki-model.json').write_text('{}',encoding='utf-8')
    (root/'app/brain_cli.py').write_text('import json,sys\nsys.stdout.reconfigure(encoding="utf-8")\nprint(json.dumps({"status":"selected","executable":{"path":'+json.dumps(path)+',"model":"m","effort":"high"},"worker":{}} if sys.argv[1]=="wiki" else {}))\n',encoding='utf-8')
    def narrow():
        raw=io.BytesIO();monkeypatch.setattr(sys,'stdout',io.TextIOWrapper(raw,encoding='cp1252'));return raw
    spec=importlib.util.spec_from_file_location('check_setup_narrow',SOURCE/'check_setup.py');check=importlib.util.module_from_spec(spec);spec.loader.exec_module(check)
    raw=narrow();monkeypatch.setattr(sys,'argv',['check_setup.py','--root',str(root),'--home',str(tmp_path/'home'),'--no-service'])
    assert check.main()==0
    sys.stdout.flush();assert 'PASS   wiki assistant: '+path+', model m, effort high' in raw.getvalue().decode('utf-8')
    setup=setup_module_under_test()
    class Done:returncode=0;stderr='';stdout=''
    monkeypatch.setattr(setup.runpy,'run_path',lambda found:{'verify':lambda repo:{'version':'0.2.10'}})
    monkeypatch.setattr(setup,'run',lambda command,capture=False:Done())
    monkeypatch.setattr(setup,'wait_running',lambda *args,**kwargs:True)
    monkeypatch.setattr(setup,'offer_wiki',lambda device,assume:'No wiki assistant is selected: '+path)
    (root/'state/wiki-model.json').unlink()
    raw=narrow();monkeypatch.setattr(sys,'argv',['setup_device.py','--root',str(root),'--clients','','--yes'])
    with pytest.raises(SystemExit) as stop:setup.main()
    sys.stdout.flush();assert stop.value.code==0 and raw.getvalue().decode('utf-8').rstrip().endswith('Still open: No wiki assistant is selected: '+path)


def test_setup_runs_the_self_test_only_when_asked_and_its_failure_fails_the_run(tmp_path,monkeypatch,capsys):
    setup=setup_module_under_test()
    root=tmp_path/'runtime';vault=tmp_path/'vault';(root/'state').mkdir(parents=True);vault.mkdir()
    (root/'device.json').write_text(json.dumps({'device_id':'abc123','role':'capture','version':'0.2.11','vault':str(vault),'python':sys.executable}),encoding='utf-8')
    asked=[];answer={}
    class Done:
        returncode=0;stderr='';stdout=''
    def brain(device,*arguments,capture=False):
        asked.append(arguments);done=Done();done.stdout=answer.get('out','');done.returncode=answer.get('code',0);done.stderr=answer.get('err','');return done
    monkeypatch.setattr(setup,'brain',brain)
    monkeypatch.setattr(setup.runpy,'run_path',lambda path:{'verify':lambda repo:{'version':'0.2.11'}})
    monkeypatch.setattr(setup,'run',lambda command,capture=False:Done())
    monkeypatch.setattr(setup,'wait_running',lambda *args,**kwargs:True)
    def go(*flags):
        asked.clear();monkeypatch.setattr(sys,'argv',['setup_device.py','--root',str(root),'--clients','',*flags])
        with pytest.raises(SystemExit) as stop:setup.main()
        return stop.value.code,capsys.readouterr().out
    assert go('--yes')==(0,go('--yes')[1]) and asked==[] and 'self-test' not in go('--yes')[1]
    rows=[{'status':'PASS','check':'self-test save','detail':'saved: 00-Inbox/AI-Captures/a.md'},{'status':'PASS','check':'self-test search','detail':'found'}]
    answer.update(out=json.dumps({'status':'passed','failed':0,'checks':rows}))
    code,said=go('--check','--selftest')
    assert code==0 and asked==[('selftest',)] and 'PASS   self-test save: saved: 00-Inbox/AI-Captures/a.md' in said and 'Self-test passed' in said
    rows[1]={'status':'FAIL','check':'self-test search','detail':'the saved milestone is not among the results'}
    answer.update(out=json.dumps({'status':'failed','failed':1,'checks':rows}),code=1)
    code,said=go('--selftest')
    assert code==1 and 'FAIL   self-test search' in said and '1 failed.' in said
    # No report at all is a failure with the runtime's last line, never a silent pass.
    answer.update(out='',code=1,err='Traceback\nOwnershipError: No owned local service is available')
    code,said=go('--selftest')
    assert code==1 and 'FAIL   self-test: it gave no report: OwnershipError: No owned local service is available' in said
    answer.update(out=json.dumps({'status':'passed','failed':0,'checks':[]}),code=0)
    assert go('--selftest')[0]==1
    code,said=go('--selftest','--no-service')
    assert code==0 and asked==[] and 'SKIP   self-test' in said


@pytest.mark.parametrize('closed_ambient_loop',[False,True],ids=['normal','closed-ambient-loop'])
def test_self_test_reports_every_step_and_stops_claiming_when_nothing_was_saved(brain,monkeypatch,closed_ambient_loop):
    """The steps run against a stand-in for the assistant connection; the real one is exercised on a device."""
    import brain_selftest as selftest
    import fastmcp,fastmcp.client.transports as transports
    from brain_client import REQUIRED
    if closed_ambient_loop:
        # Basic Memory's migration dependency patches asyncio.run to reuse the current loop.
        loop=selftest.asyncio.new_event_loop()
        loop.close()
        monkeypatch.setattr(selftest.asyncio,'run',loop.run_until_complete)
    monkeypatch.setattr(cfg,'VERSION','0.2.11')
    state={'tools':set(REQUIRED),'capture':{'status':'saved','path':'00-Inbox/AI-Captures/x.md'},'started':[]}
    class Tool:
        def __init__(self,name):self.name=name
    class Answer:
        def __init__(self,data):self.data=data
    class Client:
        def __init__(self,transport,timeout=None,init_timeout=None):state['started'].append(transport)
        async def __aenter__(self):return self
        async def __aexit__(self,*args):return False
        async def list_tools(self):return [Tool(name) for name in state['tools']]
        async def call_tool(self,name,arguments):
            if name=='brain_health':return Answer({'status':'running','version':'0.2.11'})
            if name=='brain_context':return Answer({'context':{}})
            if name=='brain_capture':
                if state['capture'] is None:raise RuntimeError('write refused')
                assert arguments==selftest.milestone();return Answer(state['capture'])
            if name=='brain_read':
                wanted=selftest.milestone()
                return Answer({'path':state.get('read_path',arguments['path']),'content':state.get('note','1: event_id: '+wanted['event_id']+'\n2: # '+wanted['title']+'\n3: '+wanted['outcome'])})
            if name=='brain_search':return Answer({'results':[{'path':state.get('hit','00-Inbox/AI-Captures/x.md')}]})
    monkeypatch.setattr(fastmcp,'Client',Client)
    monkeypatch.setattr(transports,'StdioTransport',lambda **options:options)
    report=selftest.run()
    assert report['status']=='passed' and [row['check'] for row in report['checks']]==['self-test '+name for name in ('tools','health','context','save','read','search')],report
    # The connection is the recorded Python with the adapter and nothing else, as in an assistant's settings.
    assert state['started'][0]['command']==str(cfg.PYTHON) and state['started'][0]['args']==[str(cfg.ROOT/'app/brain_client.py')]
    # The milestone is the same every time on one device, so a second run is a duplicate and still a pass.
    assert selftest.milestone()==selftest.milestone() and cfg.DEVICE_ID in selftest.milestone()['event_id']
    state['capture']={'status':'duplicate','path':'00-Inbox/AI-Captures/x.md'}
    assert selftest.run()['status']=='passed'
    state['hit']='01-Projects/Other.md'
    report=selftest.run()
    assert report['failed']==1 and report['checks'][-1]['status']=='FAIL'
    # A note that keeps only the title, or an answer for another path, is not the saved milestone.
    state.pop('hit')
    for wrong in ({'note':'1: # '+selftest.milestone()['title']},{'read_path':'00-Inbox/AI-Captures/other.md'}):
        state.update(wrong);report=selftest.run()
        assert [row['check'] for row in report['checks'] if row['status']=='FAIL']==['self-test read'],wrong
        for key in wrong:state.pop(key)
    # A connection that cannot be opened still gives all six lines, each with the reason.
    class Broken(Client):
        async def __aenter__(self):
            log=state['started'][-1].get('log_file')
            if log is not None:
                log.write('brain_process.OwnershipError: synthetic private detail\n')
                log.flush()
            raise OSError('adapter did not start')
    monkeypatch.setattr(fastmcp,'Client',Broken)
    report=selftest.run()
    assert [row['check'] for row in report['checks']]==['self-test '+name for name in selftest.STEPS] and report['failed']==6
    assert all('adapter did not start' in row['detail'] for row in report['checks'])
    assert 'adapter could not verify the local service' in report['checks'][0]['detail']
    assert all('synthetic private detail' not in row['detail'] for row in report['checks'])
    monkeypatch.setattr(fastmcp,'Client',Client)
    state['capture']=None;state['tools']=set(REQUIRED)-{'brain_capture'}
    report=selftest.run()
    failed={row['check'] for row in report['checks'] if row['status']=='FAIL'}
    assert failed=={'self-test tools','self-test save','self-test read','self-test search'} and report['status']=='failed'
