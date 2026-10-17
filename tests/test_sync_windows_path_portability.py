"""Pause shared note publication before Windows-incompatible component names.

The enumerated-name seam supplies one raw Git candidate on every OS, while all
other commands delegate to the shipping transport and local file-only Git. The
POSIX test also creates actual incompatible originals. No path is normalized or
renamed, and no source/store path policy is borrowed by these behavioral tests.
"""
import hashlib,json,os,subprocess
from pathlib import Path

import pytest
import brain_git as g
import brain_store as store
from test_sync_fsmonitor_policy import isolated_git,_raw,_init

BAD=[
 ('aux','01-Projects/aux.md'),('con','01-Projects/con.md'),('nul','01-Projects/nul.md'),
 ('prn','01-Projects/prn.md'),('com1','01-Projects/COM1.md'),('com9','01-Projects/com9.md'),
 ('lpt1','01-Projects/LPT1.md'),('lpt9','01-Projects/lpt9.md'),('mixed-case','01-Projects/AuX.md'),
 ('reserved-before-extension','01-Projects/nul.extra.md'),('reserved-directory','02-Areas/COM1/plan.md'),
 ('space-before-extension','01-Projects/con .md'),('superscript-one','01-Projects/COM¹.md'),
 ('superscript-two','01-Projects/COM².md'),('superscript-three','01-Projects/LPT³.md'),
 ('console-input','01-Projects/CONIN$.md'),('console-output','01-Projects/CONOUT$.md'),
 ('git-windows-lpt-zero','01-Projects/LPT0.md'),
 ('directory-dot','02-Areas/Ideas./plan.md'),('directory-space','02-Areas/Ideas /plan.md'),
 *[('punctuation-'+str(index),'03-Resources/reserved'+char+'name.md') for index,char in enumerate('<>:"|?*')],
 ('literal-backslash','03-Resources/regex \\d.md'),
 *[('control-'+str(ord(char)),'03-Resources/control'+char+'name.md') for char in ('\x01','\t','\n','\x1f')],
]
PREFIX='paused: Markdown path is not compatible with Windows: '
SUFFIX='. Rename it explicitly on a device that can open the original before syncing; original and staging preserved.'


def repository(brain,tmp_path,hooks):
    vault,state=brain;remote=tmp_path/'remote.git'
    _init(remote,hooks,bare=True);_init(vault,hooks)
    note=vault/'01-Projects/Ordinary.md';note.parent.mkdir();note.write_bytes(b'# Baseline\nHarmless source evidence.\n')
    _raw(vault,'add','--','01-Projects/Ordinary.md');_raw(vault,'commit','-m','synthetic baseline')
    _raw(vault,'remote','add','origin',str(remote));_raw(vault,'push','-u','origin','HEAD')
    note.write_bytes(b'# Updated ordinary\nHarmless local evidence awaiting synchronization.\n')
    return vault,state,remote,note


def snapshot(vault,remote):
    return {'head':_raw(vault,'rev-parse','HEAD'),'remote':_raw(remote,'rev-parse','refs/heads/main'),
            'index':_raw(vault,'ls-files','--stage','-z'),'status':_raw(vault,'status','--porcelain','-z'),
            'originals':{p.relative_to(vault).as_posix():p.read_bytes() for p in vault.rglob('*.md') if '.git' not in p.relative_to(vault).parts}}


def observe_transport(monkeypatch,extra=None):
    shipping=g._run_git;commands=[]
    def delegate(command,**kwargs):
        commands.append(list(command));result=shipping(command,**kwargs)
        if extra is not None and list(command)[4:]==['ls-files','--modified','--others','--exclude-standard','-z']:
            assert result.returncode==0
            return subprocess.CompletedProcess(result.args,result.returncode,extra+'\0'+result.stdout,result.stderr)
        return result
    monkeypatch.setattr(g,'_run_git',delegate)
    return commands


def assert_no_publication(commands):
    forbidden={'add','commit','fetch','merge','push','reset'}
    assert not any(len(cmd)>4 and cmd[4] in forbidden for cmd in commands),'A paused pass must not stage or publish any candidate.'


@pytest.mark.parametrize('relative',[relative for _,relative in BAD],ids=[name for name,_ in BAD])
def test_public_sync_pauses_raw_incompatible_name_before_any_stage(brain,tmp_path,monkeypatch,isolated_git,relative):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git)
    before=snapshot(vault,remote);commands=observe_transport(monkeypatch,relative)
    result=g.sync()
    assert result==PREFIX+json.dumps(relative,ensure_ascii=True)+SUFFIX
    assert_no_publication(commands)
    assert snapshot(vault,remote)==before


def test_pause_uses_redacted_raw_path_locator_without_leaking_secret_shape(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git)
    relative='03-Resources/api_key=abcdefghijklmnopqrst?note.md'
    before=snapshot(vault,remote);commands=observe_transport(monkeypatch,relative)
    result=g.sync()
    locator='[withheld locator; path_sha256='+hashlib.sha256(relative.encode('utf-8')).hexdigest()+']'
    assert result==PREFIX+locator+SUFFIX and 'abcdefghijklmnopqrst' not in result
    assert_no_publication(commands);assert snapshot(vault,remote)==before


@pytest.mark.skipif(os.name=='nt',reason='Actual Windows-incompatible original filenames require POSIX.')
def test_actual_posix_originals_remain_unstaged_and_unpublished(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git)
    for index,(_,relative) in enumerate(BAD):
        p=vault/relative;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(('# Synthetic original '+str(index)+'\nHarmless source evidence remains unchanged.\n').encode('utf-8'))
    before=snapshot(vault,remote);commands=observe_transport(monkeypatch)
    result=g.sync()
    assert result.startswith(PREFIX) and result.endswith(SUFFIX)
    assert_no_publication(commands);assert snapshot(vault,remote)==before


def test_existing_owner_staging_retains_its_prior_pause_and_exact_entries(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git)
    _raw(vault,'add','--','01-Projects/Ordinary.md');before=snapshot(vault,remote)
    commands=observe_transport(monkeypatch,'01-Projects/aux.md')
    assert g.sync()=='paused: existing staged changes preserved'
    assert_no_publication(commands);assert snapshot(vault,remote)==before


def test_supported_components_keep_real_public_sync_and_no_hidden_path_policy(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git)
    relatives=['03-Resources/.drafts/.hidden.md','03-Resources/Internal space/Unicode-مرحبا.md',
               '03-Resources/Release[#]^%notes.md','01-Projects/COM10.md','01-Projects/auxiliary.md']
    for rel in relatives:
        p=vault/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'# Supported source\nHarmless ordinary evidence.\n')
    expected={rel:(vault/rel).read_bytes() for rel in relatives+['01-Projects/Ordinary.md']}
    assert g.sync()=='synced'
    assert _raw(vault,'status','--porcelain')==b''
    assert _raw(vault,'rev-parse','HEAD')==_raw(remote,'rev-parse','refs/heads/main')
    for rel,raw in expected.items():assert _raw(remote,'show','refs/heads/main:'+rel)==raw==(vault/rel).read_bytes()
