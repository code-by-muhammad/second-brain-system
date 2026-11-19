"""A v0.2.13 device with no service can update on a non-systemd host.

systemd, macOS and Windows still refuse an absent or stopped task. The
controller here is the upgrade fixture's fake; it does not register a real service.
"""
import json
import sys
import pytest
from test_portable_remediation import portable
from upgrade_fixture import prepare


def _unregistered():
    return {'status':'inspected','task_present':False,'task_enabled':None,'registration':None,
            'native':{'backend':'user-space','file_present':False,'enabled':None,'loaded':False,
                      'running':False,'quiescent':True,'main_pid':None}}


def test_predicate_rejects_every_other_platform_and_a_stopped_service(portable,monkeypatch):
    updater=portable[1]
    native=_unregistered()['native']
    monkeypatch.setattr(updater.sys,'platform','linux')
    assert updater._unregistered_user_service(_unregistered())
    for platform in ('win32','darwin'):
        monkeypatch.setattr(updater.sys,'platform',platform)
        assert not updater._unregistered_user_service(_unregistered())
    monkeypatch.setattr(updater.sys,'platform','linux')
    stopped={**_unregistered(),'task_enabled':False,'native':{**native,'file_present':True,'enabled':False,'quiescent':True}}
    assert not updater._unregistered_user_service(stopped)
    systemd={**_unregistered(),'native':{key:value for key,value in native.items() if key!='backend'}}
    assert not updater._unregistered_user_service(systemd)


@pytest.mark.skipif(sys.platform!='linux',reason='The forward updater admits this host as Linux before the service branch.')
def test_v0213_without_a_service_updates_on_a_non_systemd_host(portable,monkeypatch):
    h=prepare(portable,monkeypatch,previous_version='0.2.13',target_version='0.2.14')
    before=(h.root/'device.json').read_bytes()
    calls=[]
    def controller(python,checkout,root,command,action=None,*flags):
        calls.append((command,action))
        if command=='health':
            return {'status':'running','version':json.loads((root/'device.json').read_text())['version']}
        if action=='inspect':return _unregistered()
        if action in ('install','start','withdraw-first','stop'):return {'status':action}
        raise AssertionError((command,action))
    monkeypatch.setattr(h.updater,'controller',controller)
    result=h.updater.perform(h.root,'v0.2.14')
    assert result['status']=='updated' and result['first_user_service'] is True and result['version']=='0.2.14'
    assert json.loads((h.root/'device.json').read_text())['version']=='0.2.14'
    assert [action for command,action in calls if command=='service']==['inspect','inspect','install','start']
    assert not (h.root/'state/service-transaction.json').exists()
    assert (h.root/'bin'/'second-brain-mcp').is_file()
    assert before!=(h.root/'device.json').read_bytes()


@pytest.mark.skipif(sys.platform!='linux',reason='The forward updater admits this host as Linux before the service branch.')
def test_stopped_user_service_and_absent_systemd_task_still_defer(portable,monkeypatch):
    h=prepare(portable,monkeypatch,previous_version='0.2.13',target_version='0.2.14')
    before=(h.root/'device.json').read_bytes()
    def controller(python,checkout,root,command,action=None,*flags):
        if action=='inspect':
            return {'status':'inspected','task_present':False,'task_enabled':False,'registration':None,
                    'native':{'backend':'user-space','file_present':True,'enabled':False,'loaded':False,
                              'running':False,'quiescent':True,'main_pid':None}}
        raise AssertionError('no service change on a refused update')
    monkeypatch.setattr(h.updater,'controller',controller)
    with pytest.raises(RuntimeError,match='before a hold was created'):h.updater.perform(h.root,'v0.2.14')
    assert (h.root/'device.json').read_bytes()==before
    def systemd(python,checkout,root,command,action=None,*flags):
        if action=='inspect':return {'status':'inspected','task_present':False,'task_enabled':None,'registration':None,'native':{'file_present':False}}
        raise AssertionError('systemd absence must not install a service')
    monkeypatch.setattr(h.updater,'controller',systemd)
    with pytest.raises(RuntimeError,match='before a hold was created'):h.updater.perform(h.root,'v0.2.14')
    assert (h.root/'device.json').read_bytes()==before
