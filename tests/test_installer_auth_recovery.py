"""Interrupted installer auth writes recover without rotating existing credentials."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


SOURCE=Path(os.environ.get('R150_SOURCE',Path(__file__).resolve().parents[1])).resolve()


def tree_bytes(path):
    return {str(p.relative_to(path)):p.read_bytes() for p in path.rglob('*') if p.is_file()}


@pytest.fixture
def auth_install(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('r150_installer',SOURCE/'install.py')
    installer=importlib.util.module_from_spec(spec); spec.loader.exec_module(installer)
    root=tmp_path/'runtime'; vault=tmp_path/'vault'; home=tmp_path/'home'
    vault.mkdir(); home.mkdir()
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    monkeypatch.setenv('APPDATA',str(home/'AppData/Roaming'))
    return installer,root,vault,home


def pair(root):
    state=root/'state'; state.mkdir(parents=True,exist_ok=True)
    token='r150-synthetic-fixture-only-credential'
    client=state/'client-access.json'; server=state/'access.json'
    client.write_text(json.dumps({'token':token,'owner_metadata':{'keep':True}},indent=2)+'\n',encoding='utf-8',newline='\n')
    server.write_text(json.dumps({'token_sha256':hashlib.sha256(token.encode()).hexdigest(),'owner_metadata':['keep']},indent=2)+'\n',encoding='utf-8',newline='\n')
    return client,server


def no_rotation(monkeypatch,installer):
    def forbidden(*args,**kwargs):
        raise AssertionError('Existing client credential must not be rotated.')
    monkeypatch.setattr(installer.secrets,'token_urlsafe',forbidden)


def assert_authenticates(client,server):
    token=json.loads(client.read_text(encoding='utf-8'))['token']
    expected=json.loads(server.read_text(encoding='utf-8'))['token_sha256']
    assert hashlib.sha256(token.encode()).hexdigest()==expected


def test_first_install_interrupted_after_client_token_retries_without_rotation(auth_install,monkeypatch):
    installer,root,vault,_=auth_install
    original=installer.atomic
    def interrupted(path,text):
        if Path(path)==root/'state/access.json':
            raise OSError('Synthetic interruption before server credential publication.')
        return original(path,text)
    monkeypatch.setattr(installer,'atomic',interrupted)
    with pytest.raises(OSError,match='Synthetic interruption'):
        installer.deploy(root,Path(sys.executable),vault)
    client=root/'state/client-access.json'; server=root/'state/access.json'
    before=client.read_bytes()
    assert before and not server.exists()
    monkeypatch.setattr(installer,'atomic',original)
    no_rotation(monkeypatch,installer)
    result=installer.deploy(root,Path(sys.executable),vault)
    assert result['status']=='installed'
    assert client.read_bytes()==before
    assert_authenticates(client,server)


def test_client_only_auth_recovers_missing_server_hash_without_rewriting_client(auth_install,monkeypatch):
    installer,root,vault,_=auth_install
    client,server=pair(root); server.unlink(); before=client.read_bytes()
    no_rotation(monkeypatch,installer)
    assert installer.deploy(root,Path(sys.executable),vault)['status']=='installed'
    assert client.read_bytes()==before
    assert_authenticates(client,server)


def test_valid_auth_pair_remains_byte_exact_on_repeated_deploy(auth_install,monkeypatch):
    installer,root,vault,_=auth_install
    client,server=pair(root); before=(client.read_bytes(),server.read_bytes())
    no_rotation(monkeypatch,installer)
    first=installer.deploy(root,Path(sys.executable),vault,'maintainer')
    second=installer.deploy(root,Path(sys.executable),vault,'capture')
    assert first['device_id']==second['device_id']
    assert second['role']=='maintainer'
    assert (client.read_bytes(),server.read_bytes())==before
    assert_authenticates(client,server)


@pytest.mark.parametrize('state',['fresh','complete','client-only'])
def test_validate_only_does_not_write_or_repair_auth(auth_install,monkeypatch,state):
    installer,root,vault,home=auth_install
    if state!='fresh':
        _,server=pair(root)
        if state=='client-only': server.unlink()
    before=(tree_bytes(root),tree_bytes(home))
    no_rotation(monkeypatch,installer)
    monkeypatch.setattr(sys,'argv',['install.py','--root',str(root),'--vault',str(vault),
        '--reuse-python',sys.executable,'--configure-clients','--validate-only'])
    installer.main()
    assert (tree_bytes(root),tree_bytes(home))==before
    if state=='fresh': assert not root.exists()


@pytest.mark.parametrize('operation',['deploy','validate-only'])
def test_server_only_auth_refuses_before_runtime_or_client_mutation(auth_install,monkeypatch,operation):
    installer,root,vault,home=auth_install
    client,_=pair(root); client.unlink()
    before=(tree_bytes(root),tree_bytes(home))
    with pytest.raises(ValueError,match='server credential has no client credential'):
        if operation=='deploy': installer.deploy(root,Path(sys.executable),vault,configure_clients=True)
        else:
            monkeypatch.setattr(sys,'argv',['install.py','--root',str(root),'--vault',str(vault),
                '--reuse-python',sys.executable,'--configure-clients','--validate-only'])
            installer.main()
    assert (tree_bytes(root),tree_bytes(home))==before


@pytest.mark.parametrize('content',['{invalid-json','{}','{"token":42}','{"token":""}',json.dumps({'token':'\ud800'})])
def test_invalid_client_only_auth_refuses_before_runtime_or_client_mutation(auth_install,content):
    installer,root,vault,home=auth_install
    client,server=pair(root); server.unlink(); client.write_text(content,encoding='utf-8')
    before=(tree_bytes(root),tree_bytes(home))
    with pytest.raises(ValueError,match='Invalid or mismatched existing authentication'):
        installer.deploy(root,Path(sys.executable),vault,configure_clients=True)
    assert (tree_bytes(root),tree_bytes(home))==before


@pytest.mark.parametrize('existing',['malformed-client','client-empty','client-surrogate',
    'malformed-server','server-array','server-hash-type','mismatched'])
@pytest.mark.parametrize('operation',['deploy','validate-only'])
def test_invalid_two_file_auth_refuses_before_runtime_or_client_mutation(auth_install,monkeypatch,existing,operation):
    installer,root,vault,home=auth_install
    client,server=pair(root)
    if existing=='malformed-client': client.write_bytes(b'{existing-invalid-client')
    elif existing=='client-empty': client.write_text(json.dumps({'token':''}),encoding='utf-8')
    elif existing=='client-surrogate': client.write_text(json.dumps({'token':'\ud800'}),encoding='utf-8')
    elif existing=='malformed-server': server.write_bytes(b'{existing-invalid-server')
    elif existing=='server-array': server.write_bytes(b'[]')
    elif existing=='server-hash-type': server.write_bytes(b'{"token_sha256":42}')
    else: server.write_bytes(b'{"token_sha256":"existing-unmatched-owner-hash"}\n')
    before=(tree_bytes(root),tree_bytes(home))
    no_rotation(monkeypatch,installer)
    with pytest.raises(ValueError,match='Invalid or mismatched existing authentication'):
        if operation=='deploy': installer.deploy(root,Path(sys.executable),vault,configure_clients=True)
        else:
            monkeypatch.setattr(sys,'argv',['install.py','--root',str(root),'--vault',str(vault),
                '--reuse-python',sys.executable,'--configure-clients','--validate-only'])
            installer.main()
    assert (tree_bytes(root),tree_bytes(home))==before
