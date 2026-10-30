"""Real installer/planner/snapshot/restore functions; explicitly fake OS boundary.

No subprocess, service, model or network operation is executed by this fixture.
Release provenance and real isolated children have separate dedicated tests.
"""
from contextlib import ExitStack,redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from filelock import FileLock,Timeout
import pytest


def prepare(portable,monkeypatch):
    install,updater,root,vault,home=portable
    source=install.REPO
    install.deploy(root,Path(sys.executable),vault)
    old=root/'old-release';old.mkdir()
    payload={'app/'+path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in (root/'app').glob('*.py')}
    (old/'release-manifest.json').write_text(json.dumps({'version':'0.1.0','sha256':payload}))
    device=json.loads((root/'device.json').read_text());device.update(version='0.1.0',release_root=str(old))
    install.atomic(root/'device.json',json.dumps(device)+'\n')
    release=root/'releases/v0.1.6';(release/'docs').mkdir(parents=True);(release/'app').mkdir()
    monkeypatch.setattr(updater,'__file__',str(release/'update.py'))
    (release/'docs/AI-PROTOCOL.md').write_bytes((source/'docs/AI-PROTOCOL.md').read_bytes())
    (release/'app/brain_cli.py').write_bytes(b'# synthetic new application, never executed\n')
    support=install.setup_helpers()
    h=SimpleNamespace(install=install,updater=updater,root=root,vault=vault,home=home,release=release,device=device,
                      events=[],health=[],running=True,partial_stop=False,last_end=False,on_wiki=None,fail_health=False)
    monkeypatch.setattr(install,'private',lambda *args:None)
    monkeypatch.setattr(support,'host_context',lambda:None)
    monkeypatch.setattr(support,'service_python',lambda value:None)
    monkeypatch.setattr(support,'desktop_busy',lambda:False)
    monkeypatch.setattr(install,'setup_helpers',lambda:support)
    monkeypatch.setattr(updater,'installer',lambda:install)
    def provision(*args):
        h.events.append({'kind':'provision'});return Path(sys.executable)
    monkeypatch.setattr(support,'provision',provision)
    monkeypatch.setattr(updater,'verify',lambda *args,**kwargs:{'version':'0.1.6',
        'capabilities':['sealed-client-refresh-v1','owned-service-control-v1'],
        'git_identity':{'tag':'v0.1.6','commit':'synthetic-not-provenance'}})

    def run(arguments,**options):
        if str(release/'install.py') in arguments:
            position=arguments.index(str(release/'install.py'))
            assert arguments[1:4]==['-I','-B','-X'] and 'utf8' in arguments
            assert '--refresh-clients' in arguments and '--configure-clients' not in arguments
            assert options['env']['BRAIN_SYSTEM_ROOT']==str(root) and options['env']['BRAIN_VAULT']==str(vault)
            previous=sys.argv;output=io.StringIO()
            install.REPO=release;install.VERSION='0.1.6'
            try:
                sys.argv=['install.py',*arguments[position+1:]]
                with redirect_stdout(output):install.main()
            finally:sys.argv=previous
            value=json.loads(output.getvalue())  # exactly one JSON receipt
            h.events.append({'kind':'installer','preflight':'--validate-only' in arguments})
            return SimpleNamespace(returncode=0,stdout=json.dumps(value),stderr='')
        if '-c' in arguments and arguments[arguments.index('-c')+2:]==['service','install']:
            # The real install.main child boundary is intercepted here. It must
            # be isolated/canonical/captured; no native action can escape.
            assert options['capture_output'] and options['env']['BRAIN_STATE']==str(root/'state')
            h.events.append({'kind':'registration child','simulated':True})
            return SimpleNamespace(returncode=0,stdout='{"status":"installed","simulated":true}',stderr='')
        pytest.fail('Unexpected setup child in pure upgrade fixture.')
    monkeypatch.setattr(support,'run',run)

    def flag(flags,name):return flags[flags.index(name)+1]
    def transaction():
        path=root/'state/service-transaction.json'
        return json.loads(path.read_text()) if path.exists() else {}
    def controller(python,checkout,selected,command,action=None,*flags):
        assert Path(checkout) in (release,root) and selected==root
        h.events.append({'kind':'controller','command':command,'action':action,'checkout':str(checkout)})
        if command=='wiki':
            if h.on_wiki is None:pytest.fail('No migration/model work authorized by this fixture case.')
            return h.on_wiki(action,flags)
        if command=='health':return {'status':'running','version':json.loads((root/'device.json').read_text())['version']}
        assert command=='service'
        path=root/'state/service-transaction.json'
        if action=='inspect':return {'status':'inspected','task_present':True,'task_enabled':True,'simulated':True}
        if action=='hold':
            install.atomic(path,json.dumps({'hold_id':flag(flags,'--hold-id'),'root':str(root),'snapshot':flag(flags,'--snapshot'),'stage':'prepared'}))
        elif action=='stop':
            h.last_end=False
            if h.partial_stop:
                h.partial_stop=False;h.last_end=True;h.running=False
                raise RuntimeError('synthetic partial native stop')
            try:
                with ExitStack() as locks:
                    for name in ('wiki-worker.lock','index-bootstrap.lock','maintenance.lock','writer.lock'):
                        locks.enter_context(FileLock(str(root/'state'/name),timeout=0))
            except Timeout:raise RuntimeError('synthetic busy writer refusal')
            h.last_end=True;h.running=False
            install.atomic(path,json.dumps({**transaction(),'stage':'stopped','empty_at_filetime':123}))
            return {'status':'stopped','empty_at_filetime':123,'simulated':True}
        elif action=='cancel':
            if h.last_end:raise RuntimeError('unverified End cannot be cancelled')
            path.unlink();return {'status':'cancelled; runtime not switched','simulated':True}
        elif action=='install':
            with pytest.raises(Timeout):
                with FileLock(str(root/'state/writer.lock'),timeout=0):pass
        elif action=='start':
            h.running=True;install.atomic(path,json.dumps({**transaction(),'stage':'started'}))
            return {'status':'started','simulated':True}
        elif action=='finish':path.unlink()
        else:pytest.fail('Unknown OS action in pure upgrade fixture.')
        return {'status':action,'simulated':True}
    monkeypatch.setattr(updater,'controller',controller)
    def wait(python,selected,version,**options):
        assert selected==root and h.running
        h.health.append(version)
        if version=='0.1.6' and h.fail_health:raise RuntimeError('new readiness failed')
        return {'status':'running','version':version,'simulated':True}
    monkeypatch.setattr(updater,'wait_health',wait)
    return h
