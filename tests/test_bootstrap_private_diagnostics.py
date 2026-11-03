"""Captured bootstrap failure output stays private; no native or source child."""
import json,subprocess
from pathlib import Path
import pytest
from test_installer_toml_bootstrap import installer,context,normal_args,tree


@pytest.mark.parametrize('binary',[False,True])
@pytest.mark.parametrize('write_fails',[False,True])
def test_failed_bootstrap_emits_only_safe_pointer_and_preserves_exitcode(tmp_path,monkeypatch,capsys,write_fails,binary):
    home,appdata,vault,root,_,forbidden=context(tmp_path,monkeypatch)
    release=tmp_path/'verified synthetic checkout';release.mkdir();monkeypatch.setattr(installer,'REPO',release)
    python=release/'.venv/Scripts/python.exe';python.parent.mkdir(parents=True);python.write_bytes(b'unexecuted fixture interpreter')
    normal_args(monkeypatch,root,vault);before=(tree(home),tree(appdata));secret='synthetic-secret-never-console'
    monkeypatch.setattr(installer.setup_helpers(),'provision',lambda *args:python)
    def run(argv,**kwargs):
        assert kwargs['capture_output'] is True and 'text' not in kwargs and 'encoding' not in kwargs
        stdout='long prefix '+secret;stderr='synthetic resume guidance '+secret
        if binary:stdout=stdout.encode()+b'\xe9';stderr=stderr.encode()+b'\xe9'
        raise subprocess.CalledProcessError(23,argv,output=stdout,stderr=stderr)
    monkeypatch.setattr(installer.setup_helpers(),'run',run)
    private=[]
    def directory(path):
        assert path==root/'logs';path.mkdir(parents=True);private.append(path)
    def atomic(path,payload,**kwargs):
        assert kwargs=={'expected_sha256':None}
        assert path.parent in private
        if write_fails:raise PermissionError('synthetic private write refused')
        path.write_text(payload,encoding='utf-8')
    monkeypatch.setattr(installer,'private_dir',directory);monkeypatch.setattr(installer,'atomic',atomic)
    with pytest.raises(SystemExit) as error:installer.main()
    streams=capsys.readouterr();assert error.value.code==23 and not streams.out
    assert secret not in streams.err and 'synthetic resume guidance' not in streams.err
    assert (tree(home),tree(appdata))==before and not (root/'device.json').exists()
    logs=list((root/'logs').glob('bootstrap-failure-*.json'))
    if write_fails:assert not logs and 'could not be saved' in streams.err
    else:
        assert len(logs)==1 and str(logs[0]) in streams.err
        data=json.loads(logs[0].read_bytes());assert data['returncode']==23
        assert secret in data['stdout_tail'] and secret in data['stderr_tail']
        if binary:assert data['stdout_tail'].endswith('\ufffd') and data['stderr_tail'].endswith('\ufffd')


def test_diagnostics_are_bounded_tails_and_empty_failure_creates_no_file(tmp_path,monkeypatch,capsys):
    root=tmp_path/'synthetic runtime';seen=[]
    def private(path):path.mkdir(parents=True);seen.append(path)
    monkeypatch.setattr(installer,'private_dir',private)
    monkeypatch.setattr(installer,'atomic',lambda path,payload,**kwargs:path.write_text(payload))
    installer._bootstrap_child_failure(root,subprocess.CalledProcessError(7,['synthetic'],output='x'*70000+'tail',stderr=b'y'*70000+b'end'))
    logs=list((root/'logs').glob('bootstrap-failure-*.json'));data=json.loads(logs[0].read_bytes())
    assert len(data['stdout_tail'])==65536 and len(data['stderr_tail'])==65536
    assert data['stdout_truncated'] and data['stderr_truncated'] and data['stdout_tail'].endswith('tail') and data['stderr_tail'].endswith('end')
    other=tmp_path/'empty-output-runtime';installer._bootstrap_child_failure(other,subprocess.CalledProcessError(9,['synthetic']))
    assert not other.exists() and 'Initial setup child failed' in capsys.readouterr().err


def test_linked_diagnostics_folder_refused_before_acl_or_private_write(tmp_path,monkeypatch,capsys):
    root=tmp_path/'synthetic runtime';directory=root/'logs';original=Path.is_symlink
    monkeypatch.setattr(Path,'is_symlink',lambda path:path==directory or original(path))
    monkeypatch.setattr(installer,'private_dir',lambda path:pytest.fail('linked diagnostics reached ACL mutation'))
    monkeypatch.setattr(installer,'atomic',lambda *args,**kwargs:pytest.fail('linked diagnostics reached write'))
    installer._bootstrap_child_failure(root,subprocess.CalledProcessError(23,['synthetic'],stderr='synthetic-secret-never-console'))
    assert 'synthetic-secret-never-console' not in capsys.readouterr().err and not root.exists()


@pytest.mark.parametrize('binary',[True,False,'crlf'])
def test_successful_bootstrap_decodes_bytes_with_replacement_and_keeps_str_fakes(tmp_path,monkeypatch,capsys,binary):
    home,appdata,vault,root,_,forbidden=context(tmp_path,monkeypatch)
    release=tmp_path/'verified synthetic checkout';release.mkdir();monkeypatch.setattr(installer,'REPO',release)
    python=release/'.venv/Scripts/python.exe';python.parent.mkdir(parents=True);python.write_bytes(b'unexecuted fixture interpreter')
    normal_args(monkeypatch,root,vault);before=(tree(home),tree(appdata))
    monkeypatch.setattr(installer.setup_helpers(),'provision',lambda *args:python)
    def run(argv,**kwargs):
        assert kwargs['capture_output'] is True and 'text' not in kwargs and 'encoding' not in kwargs
        return subprocess.CompletedProcess(argv,0,stdout=b'out\r\n' if binary=='crlf' else b'out\xe9' if binary else 'out string',stderr=b'err\r\n' if binary=='crlf' else b'err\xe9' if binary else 'err string')
    monkeypatch.setattr(installer.setup_helpers(),'run',run)
    with pytest.raises(SystemExit) as error:installer.main()
    streams=capsys.readouterr();assert error.value.code==0
    assert streams.out==('out\n' if binary=='crlf' else 'out\ufffd' if binary else 'out string') and streams.err==('err\n' if binary=='crlf' else 'err\ufffd' if binary else 'err string')
    assert (tree(home),tree(appdata))==before and not (root/'device.json').exists()


def test_nonascii_diagnostics_bound_raw_utf8_bytes_before_decoding(tmp_path,monkeypatch):
    root=tmp_path/'synthetic runtime'
    monkeypatch.setattr(installer,'private_dir',lambda path:path.mkdir(parents=True))
    monkeypatch.setattr(installer,'atomic',lambda path,payload,**kwargs:path.write_text(payload,encoding='utf-8'))
    raw=('\U0001f600'*20000+'z').encode('utf-8')
    installer._bootstrap_child_failure(root,subprocess.CalledProcessError(23,['synthetic'],output=raw,stderr=raw))
    data=json.loads(next((root/'logs').glob('bootstrap-failure-*.json')).read_bytes())
    assert data['stdout_tail']==raw[-65536:].decode('utf-8',errors='replace')
    assert data['stderr_tail']==data['stdout_tail'] and data['stdout_truncated'] and data['stderr_truncated']
    assert data['stdout_tail'].endswith('z') and len(data['stdout_tail'])<65536
