"""Public initial-setup recovery preserves identity, credentials and owner edits."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def initial(tmp_path, monkeypatch):
    source=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('fresh_resume_installer',source/'install.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    root,vault=tmp_path/'runtime',tmp_path/'vault';vault.mkdir()
    support=module.setup_helpers()
    monkeypatch.setattr(module,'setup_helpers',lambda:support)
    monkeypatch.setattr(support,'host_context',lambda:None)
    arguments=['install.py','--root',str(root),'--vault',str(vault),'--reuse-python',sys.executable]
    def run(*extra):
        monkeypatch.setattr(sys,'argv',[*arguments,*extra])
        module.main()
    return module,root,vault,run


@pytest.mark.parametrize('boundary',['device.json','state/access.json','wiki-owner','before-complete'])
def test_setup_command_resumes_the_interrupted_install(initial,monkeypatch,boundary):
    installer,root,vault,run=initial
    source=installer.REPO
    spec=importlib.util.spec_from_file_location('fresh_resume_wizard',source/'setup_device.py')
    wizard=importlib.util.module_from_spec(spec);spec.loader.exec_module(wizard)
    read_script=wizard.runpy.run_path
    monkeypatch.setattr(wizard,'runpy',SimpleNamespace(run_path=lambda path:
        {'verify':lambda path:{'version':installer.VERSION}} if Path(path).name=='update.py' else read_script(path)))
    atomic=installer.atomic

    control=vault/'02-Areas/AI-Memory/Wiki/Control.md'
    journal=root/'state/install-transaction.json'
    def interrupted(path,*args,**kwargs):
        if boundary=='before-complete' and Path(path)==journal and json.loads(args[0])['stage']=='complete':
            raise OSError('Synthetic interruption')
        if Path(path)==(control if boundary=='wiki-owner' else root/boundary):raise OSError('Synthetic interruption')
        return atomic(path,*args,**kwargs)

    monkeypatch.setattr(installer,'atomic',interrupted)
    with pytest.raises(OSError,match='Synthetic interruption'):
        run('--timezone','UTC','--role','maintainer','--initialize-wiki-owner')
    monkeypatch.setattr(installer,'atomic',atomic)
    before=json.loads(journal.read_text())

    # Use the interpreter already running this fixture in place of its own retained venv.
    monkeypatch.setattr(wizard,'retained_python',lambda:Path(sys.executable),raising=False)

    def execute(command,*,capture=False):
        if Path(command[3]).name=='install.py':
            monkeypatch.setattr(sys,'argv',[str(arg) for arg in command[3:]])
            installer.main()
        return SimpleNamespace(returncode=0,stdout='{"deferred": []}',stderr='')

    monkeypatch.setattr(wizard.shutil,'which',lambda name:sys.executable)
    monkeypatch.setattr(wizard,'run',execute)
    args=['setup_device.py','--root',str(root),'--yes','--no-service','--clients','']
    for extra in (['--check'],['--timezone','Europe/Berlin']):
        monkeypatch.setattr(sys,'argv',[*args,*extra])
        with pytest.raises(SystemExit) as refused:wizard.main()
        assert refused.value.code==2
        assert json.loads(journal.read_text())==before
    monkeypatch.setattr(sys,'argv',args)
    with pytest.raises(SystemExit) as result:wizard.main()
    assert result.value.code==0
    after=json.loads(journal.read_text())
    assert after['stage']=='complete' and after['install_id']==before['install_id']
    expected=json.loads(before['writes']['device.json'])
    assert json.loads((root/'device.json').read_text())==expected
    assert (root/'state/access.json').read_text()==before['writes']['state/access.json']
    assert 'maintainer_device_id: '+expected['device_id'] in control.read_text()


def test_initial_wiki_ownership_preserves_a_competing_owner(initial):
    _,root,vault,run=initial
    control=vault/'02-Areas/AI-Memory/Wiki/Control.md'
    control.parent.mkdir(parents=True)
    control.write_bytes(b'---\nmaintainer_device_id: other-device\n---\n')
    before=control.read_bytes()
    with pytest.raises(ValueError,match='notes do not confirm this device'):
        run('--role','maintainer','--initialize-wiki-owner')
    assert control.read_bytes()==before
    assert json.loads((root/'state/install-transaction.json').read_text())['stage']!='complete'


@pytest.mark.parametrize('boundary',['device.json','state/client-access.json','state/access.json','state/config.json','state/.bmignore'])
def test_public_resume_after_each_published_artifact(initial,monkeypatch,boundary):
    module,root,_,run=initial;original=module.atomic
    once=[True]
    def interrupted(path,text,**kwargs):
        original(path,text,**kwargs)
        if Path(path)==root/boundary and once[0]:
            once[0]=False;raise OSError('Synthetic setup interruption after publication')
    monkeypatch.setattr(module,'atomic',interrupted)
    with pytest.raises(OSError,match='Synthetic setup interruption'):run()
    journal=json.loads((root/'state/install-transaction.json').read_text())
    expected_device=json.loads(journal['writes']['device.json'])
    expected_auth=journal['writes']['state/client-access.json'].encode()
    def no_rotation(*args,**kwargs):raise AssertionError('Resume rotated planned credentials')
    monkeypatch.setattr(module.secrets,'token_urlsafe',no_rotation)
    run('--resume-install',journal['install_id'])
    assert json.loads((root/'device.json').read_text())==expected_device
    assert (root/'state/client-access.json').read_bytes()==expected_auth
    completed=json.loads((root/'state/install-transaction.json').read_text())
    assert completed['stage']=='complete' and 'writes' not in completed
    with pytest.raises(ValueError,match='complete'):run('--resume-install',journal['install_id'])


@pytest.mark.parametrize('changed',['device','auth','source','uuid','role','app'])
def test_unsafe_resume_refuses_before_mutation(initial,monkeypatch,changed):
    module,root,_,run=initial;original=module.atomic
    def interrupted(path,text,**kwargs):
        if Path(path)==root/'state/access.json':raise OSError('Synthetic interruption')
        original(path,text,**kwargs)
    monkeypatch.setattr(module,'atomic',interrupted)
    with pytest.raises(OSError):run()
    journal=json.loads((root/'state/install-transaction.json').read_text())
    if changed=='device':
        p=root/'device.json';d=json.loads(p.read_text());d['timezone']='UTC';p.write_text(json.dumps(d))
    elif changed=='auth':(root/'state/client-access.json').write_text('{"token":"owner-edited"}')
    elif changed=='app':(root/'app/owner-extra.py').write_text('# preserved owner edit')
    elif changed=='source':monkeypatch.setattr(module,'_fresh_source',lambda:{'changed':'a'*64})
    extra=['--resume-install','f'*32 if changed=='uuid' else journal['install_id']]
    if changed=='role':extra+=['--role','maintainer']
    before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):run(*extra)
    assert before=={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_model_failure_resumes_without_reprovisioning(initial,monkeypatch):
    module,root,_,run=initial;support=module.setup_helpers();actual=support.run;calls=[]
    def model(command,**kwargs):
        if '-c' in command and 'TextEmbedding' in command[command.index('-c')+1]:
            calls.append('model')
            if len(calls)==1:raise OSError('Synthetic model failure')
            return None
        return actual(command,**kwargs)
    monkeypatch.setattr(support,'run',model)
    with pytest.raises(OSError,match='model'):run('--download-model')
    journal=json.loads((root/'state/install-transaction.json').read_text())
    auth=(root/'state/client-access.json').read_bytes()
    run('--download-model','--resume-install',journal['install_id'])
    assert calls==['model','model'] and (root/'state/client-access.json').read_bytes()==auth
    assert json.loads((root/'state/install-transaction.json').read_text())['stage']=='complete'


@pytest.mark.parametrize('outcome',['absent','owned','foreign','disabled','unknown'])
def test_uncertain_registration_requires_positive_native_inspection(initial,monkeypatch,outcome):
    module,root,_,run=initial;support=module.setup_helpers();calls=[]
    monkeypatch.setattr(support,'service_python',lambda python:None)
    def control(command,**kwargs):
        action=command[-1];calls.append(action)
        if calls==['install']:raise OSError('Synthetic registration response lost')
        if action=='inspect':
            if outcome=='foreign':raise ValueError('Task belongs to another runtime; preserve it')
            record={'status':'unavailable' if outcome=='unknown' else 'inspected',
                'task_present':outcome!='absent','task_enabled':outcome!='disabled'}
            if sys.platform in ('darwin','linux'):
                record.update(native={'file_present':outcome!='absent',
                    'definition_sha256':'a'*64 if outcome!='absent' else None},
                    expected_native_definition_sha256='a'*64)
            return SimpleNamespace(stdout=json.dumps(record))
        assert action=='install' and outcome=='absent'
        return SimpleNamespace(stdout='{"status":"installed"}')
    monkeypatch.setattr(support,'run',control)
    with pytest.raises(OSError,match='registration'):run('--install-service')
    path=root/'state/install-transaction.json';journal=json.loads(path.read_text())
    assert journal['stage']=='service-intent'
    if outcome in ('foreign','disabled','unknown'):
        before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
        with pytest.raises(ValueError):run('--install-service','--resume-install',journal['install_id'])
        assert before=={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
        assert calls==['install','inspect']
    else:
        run('--install-service','--resume-install',journal['install_id'])
        assert json.loads(path.read_text())['stage']=='complete'
        assert calls==(['install','inspect','install'] if outcome=='absent' else ['install','inspect'])


def test_completed_client_configuration_does_not_block_later_model_resume(initial,monkeypatch):
    module,root,_,run=initial;support=module.setup_helpers();actual=support.run
    home=Path.home();(home/'.codex').mkdir(parents=True,exist_ok=True)
    client=home/'.codex/config.toml';client.write_text('model = "synthetic-owner"\n')
    calls=[]
    def model(command,**kwargs):
        if '-c' in command and 'TextEmbedding' in command[command.index('-c')+1]:
            calls.append('model')
            if len(calls)==1:raise OSError('Synthetic model interruption')
            return None
        return actual(command,**kwargs)
    monkeypatch.setattr(support,'run',model)
    options=['--configure-clients','--client','codex','--download-model']
    with pytest.raises(OSError):run(*options)
    journal=json.loads((root/'state/install-transaction.json').read_text())
    owner_edit=client.read_bytes()+b'\n# Owner edited after setup\n';client.write_bytes(owner_edit)
    run(*options,'--resume-install',journal['install_id'])
    assert client.read_bytes()==owner_edit
    assert json.loads((root/'state/install-transaction.json').read_text())['stage']=='complete'


def test_unpublished_race_can_be_inspected_without_secrets_and_explicitly_abandoned(initial,monkeypatch,capsys):
    module,root,_,run=initial;actual=module.atomic
    home=Path.home();(home/'.codex').mkdir(parents=True,exist_ok=True)
    client=home/'.codex/config.toml';client.write_text('model = "owner-before"\n')
    def raced(path,text):
        actual(path,text)
        if Path(path)==root/'state/install-transaction.json' and json.loads(text).get('stage')=='prepared':
            client.write_text('model = "owner-after"\n')
    monkeypatch.setattr(module,'atomic',raced)
    with pytest.raises(ValueError,match='Client settings changed'):run('--configure-clients','--client','codex')
    journal=json.loads((root/'state/install-transaction.json').read_text())
    run('--inspect-install')
    output=capsys.readouterr().out
    assert json.loads(output)['install_id']==journal['install_id']
    assert json.loads(journal['writes']['state/client-access.json'])['token'] not in output
    run('--abandon-install',journal['install_id'])
    assert not (root/'device.json').exists() and not (root/'state/install-transaction.json').exists()
    archived=json.loads((root/'state/install-transactions'/(journal['install_id']+'.json')).read_text())
    assert 'writes' not in archived and 'client_plan' not in archived
    assert client.read_text()=='model = "owner-after"\n'


def test_resume_normalizes_relative_python_before_identity_comparison(initial,monkeypatch):
    module,root,_,run=initial;actual=module.atomic
    def interrupted(path,text,**kwargs):
        actual(path,text,**kwargs)
        if Path(path)==root/'device.json':raise OSError('Synthetic interruption')
    monkeypatch.setattr(module,'atomic',interrupted)
    with pytest.raises(OSError):run()
    journal=json.loads((root/'state/install-transaction.json').read_text())
    monkeypatch.setattr(module,'atomic',actual)
    monkeypatch.chdir(Path(sys.executable).parent)
    run('--reuse-python',Path(sys.executable).name,'--resume-install',journal['install_id'])
    assert json.loads((root/'state/install-transaction.json').read_text())['stage']=='complete'
