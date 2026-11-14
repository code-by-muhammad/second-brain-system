"""The plan finishes a client: block, skill and shared rules files. Synthetic homes only."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import pytest

SOURCE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('shared_install',SOURCE/'install.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
helpers=installer.setup_helpers()
BLOCK=helpers.BEGIN+'\nold protocol\n'+helpers.END+'\n'


@pytest.fixture
def owner(tmp_path,monkeypatch):
    home=tmp_path/'home';home.mkdir()
    monkeypatch.setattr(helpers,'desktop_busy',lambda:False)
    return home,tmp_path/'runtime',tmp_path/'python.exe'


def plan(owner,**options):
    home,root,python=owner
    return helpers.plan(root,python,'new protocol',installer._codex_toml_plan,home=home,skill='new skill\n',**options)


def by_path(result):return {row['path']:row for row in result['files']}


def test_claude_code_gets_connection_block_and_skill(owner):
    home,_,_=owner
    (home/'.claude').mkdir();(home/'.claude/CLAUDE.md').write_text('# My rules\n')
    files=by_path(plan(owner,selected=['claude-code']))
    assert set(files)=={str(home/'.claude.json'),str(home/'.claude/CLAUDE.md'),str(home/'.claude/skills/second-brain/SKILL.md')}
    block=files[str(home/'.claude/CLAUDE.md')]['after']
    assert block.startswith('# My rules\n') and block.count(helpers.BEGIN)==1 and 'new protocol' in block
    assert files[str(home/'.claude/skills/second-brain/SKILL.md')]['after']=='new skill\n'


def test_refresh_adds_no_block_and_no_skill(owner):
    home,root,python=owner
    (home/'.claude').mkdir();(home/'.claude/CLAUDE.md').write_text('# My rules\n')
    (home/'.claude.json').write_text(json.dumps({'mcpServers':{'second-brain':{'command':'old','args':[str(root/'app/brain_client.py')]}}}))
    result=plan(owner,selected=['claude-code'],refresh=True)
    assert [row['path'] for row in result['files']]==[str(home/'.claude.json')]
    assert {row['reason'] for row in result['skipped']}=={'no managed protocol block','absent; refresh creates no client file'}


def test_block_in_an_imported_rules_file_is_refreshed_there_once(owner):
    """Claude imports the shared file and Codex links to it: one block, written once."""
    home,_,_=owner
    for folder in ('.agents','.claude','.codex'):(home/folder).mkdir()
    shared=home/'.agents/RULES.md';shared.write_text('# Shared\n\n'+BLOCK)
    (home/'.claude/CLAUDE.md').write_text('@~/.agents/RULES.md\n')
    os.symlink(shared,home/'.codex/AGENTS.md')
    result=plan(owner,selected=['claude-code','codex'])
    files=by_path(result)
    assert str(home/'.claude/CLAUDE.md') not in files and str(home/'.codex/AGENTS.md') not in files
    after=files[str(shared.resolve())]['after']
    assert after.count(helpers.BEGIN)==1 and 'new protocol' in after and 'old protocol' not in after
    assert any(row['reason']=='shared file already planned' for row in result['skipped'])
    helpers.validate_plan(result,owner[1])


def test_inline_import_counts_and_an_import_outside_home_or_in_a_code_block_does_not(owner,tmp_path):
    home,_,_=owner
    for folder in ('.agents','.claude'):(home/folder).mkdir()
    (home/'.agents/RULES.md').write_text(BLOCK)
    outside=tmp_path/'outside.md';outside.write_text('# No block here\n')
    (home/'.claude/CLAUDE.md').write_text('Team rules: @~/.agents/RULES.md\n@'+str(outside)+'\n```\n@~/.agents/OTHER.md\n```\nMail me @example\n')
    (home/'.agents/OTHER.md').write_text(BLOCK)
    files=by_path(plan(owner,selected=['claude-code']))
    assert str(home/'.agents/RULES.md') in files and str(home/'.claude/CLAUDE.md') not in files
    assert outside.read_text()=='# No block here\n'


def test_import_that_climbs_out_of_home_is_never_written(owner,tmp_path):
    home,root,_=owner
    (home/'.claude').mkdir();outside=tmp_path/'outside.md';outside.write_text(BLOCK)
    (home/'.claude/CLAUDE.md').write_text('@../../outside.md\n')
    result=plan(owner,selected=['claude-code'])
    assert all(Path(row['path']).resolve().is_relative_to(home.resolve()) for row in result['files'])
    # Claude already loads that block: it is reported, not refreshed out there and not doubled in here.
    assert any('outside the home folder' in row['reason'] for row in result['skipped'])
    helpers.apply(result,installer.atomic,lambda path,root:None,root)
    assert outside.read_text()==BLOCK and (home/'.claude/CLAUDE.md').read_text()=='@../../outside.md\n'


def test_first_block_goes_once_into_the_file_both_assistants_share(owner):
    """No block anywhere yet: Claude imports the shared file and Codex links to it."""
    home,root,_=owner
    for folder in ('.agents','.claude','.codex'):(home/folder).mkdir()
    shared=home/'.agents/RULES.md';shared.write_text('# Shared\n')
    (home/'.claude/CLAUDE.md').write_text('@~/.agents/RULES.md\n')
    os.symlink(shared,home/'.codex/AGENTS.md')
    result=plan(owner,selected=['claude-code','codex'])
    helpers.apply(result,installer.atomic,lambda path,root:None,root)
    loaded=(home/'.claude/CLAUDE.md').read_text()+shared.read_text()
    assert loaded.count(helpers.BEGIN)==1 and shared.read_text().count(helpers.BEGIN)==1
    assert (home/'.codex/AGENTS.md').is_symlink()


def test_two_loaded_blocks_are_reported_and_left_alone_without_stopping_the_rest(owner):
    home,_,_=owner
    for folder in ('.agents','.claude'):(home/folder).mkdir()
    (home/'.agents/RULES.md').write_text(BLOCK)
    (home/'.claude/CLAUDE.md').write_text('@~/.agents/RULES.md\n'+BLOCK)
    result=plan(owner,selected=['claude-code'])
    assert any('more than one file' in row['reason'] for row in result['skipped'])
    assert str(home/'.claude.json') in by_path(result) and not any(row['path'].endswith(('CLAUDE.md','RULES.md')) for row in result['files'])


def test_link_that_leaves_the_home_folder_is_never_written_and_does_not_stop_the_rest(owner,tmp_path):
    home,_,_=owner
    (home/'.codex').mkdir();outside=tmp_path/'outside.md';outside.write_text('# Elsewhere\n')
    os.symlink(outside,home/'.codex/AGENTS.md')
    result=plan(owner,selected=['codex'])
    assert any('leaves the home folder' in row['reason'] for row in result['skipped'])
    assert [Path(row['path']).name for row in result['files']]==['config.toml','SKILL.md'] and outside.read_text()=='# Elsewhere\n'


def test_one_shared_skill_copy_is_written_once(owner):
    home,_,_=owner
    shared=home/'.agents/skills/second-brain';shared.mkdir(parents=True);(shared/'SKILL.md').write_text('old skill\n')
    for client in ('.claude','.codex'):
        (home/client/'skills').mkdir(parents=True);os.symlink(shared,home/client/'skills/second-brain')
    result=plan(owner,selected=['claude-code','codex'])
    skills=[row for row in result['files'] if row['path'].endswith('SKILL.md')]
    assert [row['path'] for row in skills]==[str((shared/'SKILL.md').resolve())] and skills[0]['after']=='new skill\n'


def test_clients_only_adds_missing_pieces_to_an_installed_device(owner,monkeypatch,capsys):
    """An installed device can gain a client later; nothing is redeployed and the service is not touched."""
    home,root,python=owner;root.mkdir();vault=root.parent/'vault';vault.mkdir()
    device={'device_id':'fixture','version':installer.VERSION,'python':sys.executable,'vault':str(vault),
            'role':'capture','release_root':str(SOURCE),'timezone':'UTC'}
    (root/'device.json').write_text(json.dumps(device));(root/'state').mkdir()
    monkeypatch.setattr(helpers,'host_context',lambda:None)
    monkeypatch.setattr(helpers,'provision',lambda *args:pytest.fail('Clients-only must not provision'))
    monkeypatch.setattr(installer,'setup_helpers',lambda:helpers)
    monkeypatch.setattr(installer,'_qualified_platform',lambda:None)
    monkeypatch.setattr(installer,'private',lambda *args,**kwargs:None)
    monkeypatch.setattr(installer.Path,'home',classmethod(lambda cls:home))
    monkeypatch.setattr(helpers.Path,'home',classmethod(lambda cls:home))
    monkeypatch.setattr(sys,'argv',['install.py','--root',str(root),'--vault',str(vault),'--configure-clients','--clients-only',
                                    '--client','claude-code','--include-hooks','--reuse-python',sys.executable])
    installer.main()
    receipt=json.loads(capsys.readouterr().out)
    assert receipt['status']=='clients refreshed' and not receipt['client_result']['deferred']
    assert json.loads((home/'.claude.json').read_text())['mcpServers']['second-brain']['args']==[str(root/'app/brain_client.py')]
    hooks=json.loads((home/'.claude/settings.json').read_text())['hooks']
    assert set(hooks)=={'SessionStart','Stop'}
    assert (home/'.claude/CLAUDE.md').read_text().count(helpers.BEGIN)==1
    assert (home/'.claude/skills/second-brain/SKILL.md').read_bytes()==(SOURCE/'skills/second-brain/SKILL.md').read_bytes()
    assert json.loads((root/'device.json').read_text())==device


def test_new_device_uses_the_system_timezone_and_never_a_silent_default(monkeypatch):
    import types
    assert installer.local_timezone('Europe/Berlin')=='Europe/Berlin'
    with pytest.raises(ValueError,match='--timezone'):installer.local_timezone('Mars/Olympus')
    assert installer.device_settings({'timezone':'Europe/Paris'},Path('v'),Path('p'))['timezone']=='Europe/Paris'
    # A device installed before the key existed has been running on the old built-in zone; an update keeps that.
    assert installer.device_settings({'device_id':'old'},Path('v'),Path('p'))['timezone']=='Asia/Karachi'
    monkeypatch.setitem(sys.modules,'tzlocal',types.SimpleNamespace(get_localzone_name=lambda:'Pacific/Auckland'))
    assert installer.device_settings({},Path('v'),Path('p'))['timezone']=='Pacific/Auckland'
    assert installer.device_settings({},Path('v'),Path('p'),timezone='Europe/Berlin')['timezone']=='Europe/Berlin'
    monkeypatch.setitem(sys.modules,'tzlocal',types.SimpleNamespace(get_localzone_name=lambda:'local'))
    with pytest.raises(ValueError,match='--timezone'):installer.device_settings({},Path('v'),Path('p'))
    monkeypatch.setitem(sys.modules,'tzlocal',None)  # The package is absent: refuse, do not guess.
    with pytest.raises(ValueError,match='--timezone'):installer.device_settings({},Path('v'),Path('p'))


def test_updater_shows_why_its_controller_refused(tmp_path,monkeypatch):
    import types
    spec=importlib.util.spec_from_file_location('shared_update',SOURCE/'update.py')
    updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)
    support=updater.installer().setup_helpers()
    refused=types.SimpleNamespace(returncode=1,stdout='',stderr='Traceback (most recent call last):\n  ...\nRegistrationError: Native manager process ownership is unavailable.\n')
    monkeypatch.setattr(support,'isolated_python',lambda python,root:[str(python),'-I'])
    monkeypatch.setattr(support,'run',lambda *args,**kwargs:refused)
    monkeypatch.setattr(updater,'cli_environment',lambda root=None:{})
    with pytest.raises(RuntimeError,match='refused service inspect .exit 1.: RegistrationError: Native manager process ownership is unavailable'):
        updater.controller(tmp_path/'python',tmp_path,tmp_path,'service','inspect')
