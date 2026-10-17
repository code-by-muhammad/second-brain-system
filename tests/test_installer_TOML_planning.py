"""Valid TOML managed-path replacement must preserve unrelated owner settings."""
from pathlib import Path
import copy,hashlib,importlib.util,json,os,sys,tomllib
import pytest

SOURCE=Path(os.environ.get('R133_SOURCE',Path(__file__).resolve().parents[1])).resolve()
spec=importlib.util.spec_from_file_location('r133_exact_installer',SOURCE/'install.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
OLD='command = "old"\nargs = ["old.py"]\n'
OTHER='\n[mcp_servers.other]\ncommand = "keep"\n'
PREFIX='# Owner configuration comment must remain.\nmodel = "owner-choice"\n'
SHAPES=[
    ('bare-supported',PREFIX+'[mcp_servers.second-brain]\n'+OLD+OTHER),
    ('basic-quoted-supported',PREFIX+'[mcp_servers."second-brain"]\n'+OLD+OTHER),
    ('literal-quoted-key',PREFIX+"[mcp_servers.'second-brain']\n"+OLD+OTHER),
    ('escaped-basic-key',PREFIX+'[mcp_servers."second\\u002dbrain"]\n'+OLD+OTHER),
    ('spaced-dotted-header',PREFIX+'[mcp_servers . second-brain]\n'+OLD+OTHER),
    ('indented-header',PREFIX+'  [mcp_servers.second-brain]\n'+OLD+OTHER),
    ('tab-indented-header',PREFIX+'\t[mcp_servers.second-brain]\n'+OLD+OTHER),
    ('quoted-parent',PREFIX+'["mcp_servers".second-brain]\n'+OLD+OTHER),
    ('escaped-parent',PREFIX+'["mcp\\u005fservers".second-brain]\n'+OLD+OTHER),
    ('inner-bracket-whitespace',PREFIX+'[ mcp_servers.second-brain ]\n'+OLD+OTHER),
    ('both-literal-components',PREFIX+"['mcp_servers'.'second-brain']\n"+OLD+OTHER),
    ('root-dotted-assignments',PREFIX+'mcp_servers.second-brain.command = "old"\nmcp_servers.second-brain.args = ["old.py"]\nmcp_servers.other.command = "keep"\n'),
    ('parent-dotted-assignments',PREFIX+'[mcp_servers]\nsecond-brain.command = "old"\nsecond-brain.args = ["old.py"]\nother.command = "keep"\n'),
    ('parent-inline-assignment',PREFIX+'[mcp_servers]\nsecond-brain = {command = "old", args = ["old.py"], env = {OLD = "value"}}\nother = {command = "keep"}\n'),
    ('root-inline-assignment',PREFIX+'mcp_servers = { second-brain = {command = "old", args = ["old.py"]}, other = {command = "keep"} }\n'),
    ('quoted-nested-env-must-be-replaced',PREFIX+'[mcp_servers.second-brain]\n'+OLD+"[mcp_servers.'second-brain'.env]\nOLD = \"value\"\n"+OTHER),
    ('spaced-nested-env-must-be-replaced',PREFIX+'[mcp_servers.second-brain]\n'+OLD+'[mcp_servers . second-brain . env]\nOLD = "value"\n'+OTHER),
    ('header-comment-supported',PREFIX+'[mcp_servers.second-brain] # existing managed table\n'+OLD+OTHER),
]
PRESERVATION=[
    ('fake-header-multiline-basic',PREFIX+'owner_note = """\n[mcp_servers.second-brain]\ncommand = "example only"\n"""\n'+OTHER),
    ('fake-header-multiline-literal',PREFIX+"owner_note = "+("'"*3)+"\n[mcp_servers.second-brain]\ncommand = \"example only\"\n"+("'"*3)+"\n"+OTHER),
    ('fake-indented-header-multiline-basic',PREFIX+'owner_note = """\n  [mcp_servers.second-brain]\ncommand = "example only"\n"""\n'+OTHER),
    ('header-looking-comment',PREFIX+'# [mcp_servers.second-brain]\n'+OTHER),
    ('header-looking-single-line-string',PREFIX+'owner_note = "[mcp_servers.second-brain]"\n'+OTHER),
    ('similar-server-name',PREFIX+'[mcp_servers.second-brain-extra]\ncommand = "keep-similar"\n'+OTHER),
    ('case-distinct-server-name',PREFIX+'[mcp_servers.Second-Brain]\ncommand = "keep-case"\n'+OTHER),
    ('literal-root-key-with-dots',PREFIX+"'mcp_servers.second-brain' = {command = \"keep-root-key\"}\n"+OTHER),
]

def tree_bytes(root):
    return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}

def inputs(tmp_path,monkeypatch,text,platform='linux'):
    assert len(text.encode('utf-8'))<2000
    home=tmp_path/'fake home';home.mkdir();(home/'.codex').mkdir();(home/'.claude').mkdir()
    appdata=tmp_path/'fake appdata';(appdata/'Claude').mkdir(parents=True)
    monkeypatch.setenv('APPDATA',str(appdata));monkeypatch.setattr(Path,'home',classmethod(lambda cls:home));monkeypatch.setattr(sys,'platform',platform)
    def refused(*args,**kwargs):raise AssertionError('Planner must not write clients or execute commands.')
    monkeypatch.setattr(installer,'atomic',refused);monkeypatch.setattr(installer,'clients',refused);monkeypatch.setattr(installer.subprocess,'run',refused);monkeypatch.setattr(installer.subprocess,'Popen',refused)
    path=home/'.codex/config.toml';path.write_bytes(text.encode('utf-8'))
    (home/'.claude.json').write_text(json.dumps({'theme':'owner','mcpServers':{'other':{'command':'keep'}}}),encoding='utf-8')
    (home/'.claude/settings.json').write_text(json.dumps({'theme':'owner','hooks':{'Stop':[{'hooks':[{'type':'command','command':'keep-owner-hook'}]}]}}),encoding='utf-8')
    (home/'.codex/AGENTS.md').write_text('Owner instructions remain.\n',encoding='utf-8')
    root=tmp_path/'synthetic runtime';python=root/'python with spaces.exe'
    return home,appdata,root,python,path

def expected_state(data,root,python):
    expected=copy.deepcopy(data)
    expected.setdefault('mcp_servers',{})['second-brain']={'command':str(python),'args':[str(root/'app/brain_client.py')],'startup_timeout_sec':60,'tool_timeout_sec':120}
    return expected

def planned_toml(home,root,python):
    plan=installer.client_plan(root,python,home)
    return next(value for path,value in plan if path==home/'.codex/config.toml')

def check_case(tmp_path,monkeypatch,name,text,platform='linux'):
    original=tomllib.loads(text)
    assert isinstance(original,dict) and isinstance(original.get('mcp_servers',{}),dict)
    home,appdata,root,python,path=inputs(tmp_path,monkeypatch,text,platform)
    before_home=tree_bytes(home);before_appdata=tree_bytes(appdata)
    outcome='returned';result=None;error_type=None
    try:result=planned_toml(home,root,python)
    except (ValueError,TypeError,KeyError) as error:outcome='raised';error_type=type(error).__name__
    after_home=tree_bytes(home);after_appdata=tree_bytes(appdata)
    observation={'case':name,'platform':platform,'valid_original_TOML':True,'source_bytes':len(text.encode()),'planner_outcome':outcome,'error_type':error_type,'fake_home_unchanged':before_home==after_home,'fake_appdata_unchanged':before_appdata==after_appdata,'actual_client_writes':0,'actual_cli_or_provider_calls':0,'real_home_or_APPDATA_used':False}
    if result is not None:
        actual=tomllib.loads(result);observation['planned_semantics_equal']=actual==expected_state(original,root,python);observation['owner_comment_retained']='# Owner configuration comment must remain.' in result
        observation['managed_exactly_one_logical_server']=isinstance(actual['mcp_servers']['second-brain'],dict)
        # A second planning pass reads only a synthetic copy of first output.
        path.write_text(result,encoding='utf-8',newline='')
        try:again=planned_toml(home,root,python);observation['second_plan_semantics_equal']=tomllib.loads(again)==actual
        finally:path.write_bytes(before_home['.codex/config.toml'])
    (tmp_path/'observation.json').write_text(json.dumps(observation,indent=2)+'\n',encoding='utf-8')
    assert observation['fake_home_unchanged'] and observation['fake_appdata_unchanged']
    assert outcome=='returned','Valid equivalent TOML managed paths must be replaceable without duplicate table errors.'
    assert observation['planned_semantics_equal'],'Only the intended logical managed server may change.'
    assert observation['owner_comment_retained'] and observation['second_plan_semantics_equal']
    assert tree_bytes(home)==before_home and tree_bytes(appdata)==before_appdata

@pytest.mark.parametrize('name,text',SHAPES,ids=[name for name,_ in SHAPES])
def test_valid_TOML_managed_server_spellings_replace_same_logical_path_without_writes(tmp_path,monkeypatch,name,text):
    check_case(tmp_path,monkeypatch,name,text)

@pytest.mark.parametrize('name,text',PRESERVATION,ids=[name for name,_ in PRESERVATION])
def test_TOML_owner_literals_comments_and_other_servers_remain_intact(tmp_path,monkeypatch,name,text):
    check_case(tmp_path,monkeypatch,name,text)

@pytest.mark.parametrize('platform',['win32','darwin','linux'])
def test_client_plan_is_read_only_with_all_platform_paths_redirected(tmp_path,monkeypatch,platform):
    check_case(tmp_path,monkeypatch,'platform-'+platform,PREFIX+OTHER,platform)
