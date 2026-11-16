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
                           ({'is_error':False,'result':'plain text only'},'plain text only'),({'is_error':False},'no structured output')):
        reply.write_text(json.dumps(value),encoding='utf-8')
        assert expected in w._claude_output(reply,output) and not output.exists()
    reply.write_text('not json',encoding='utf-8')
    assert w._claude_output(reply,output)=='unreadable result'
    assert w._model_auth_quota_failure(1,'\nInvalid API key · Please run /login\n','packet')


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
    setup.make_maintainer({**device,'root':str(root)},root,vault,False)  # Its own ownership is accepted again.
    (vault/setup.CONTROL).write_text('---\nmaintainer_device_id: other99\n---\n',encoding='utf-8')
    (root/'device.json').write_text(json.dumps(device),encoding='utf-8')
    with pytest.raises(SystemExit,match='another device'):setup.make_maintainer({**device,'root':str(root)},root,vault,True)
    assert json.loads((root/'device.json').read_text())['role']=='capture'


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
