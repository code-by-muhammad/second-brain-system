"""Mac manager authority: pure deterministic grammar and orchestration controls."""
import plistlib
import os
import hashlib
from pathlib import Path,PurePosixPath
from types import SimpleNamespace

import pytest
import brain_mac_registration as native


LABEL='local.second-brain.test.'+'a'*32
DOMAIN='gui/501'
ADDRESS=DOMAIN+'/'+LABEL


def loaded(**changes):
    fields={'path':'/Users/fixture/Library/LaunchAgents/'+LABEL+'.plist','type':'LaunchAgent',
        'state':'not running','program':'/selected/python','working directory':'/fixture/app',
        'stdout path':'/fixture/logs/service-supervisor.log','stderr path':'/fixture/logs/service-supervisor.log',
        'domain':DOMAIN+' [100003]'}
    fields.update(changes)
    rows=[ADDRESS+' = {']+[key+' = '+value for key,value in fields.items()]
    rows+=['arguments = {','/selected/python','-I','-B','-c',native.SERVICE_ENTRY,'/fixture/app/brain_supervisor.py','}',
        'inherited environment = {','SSH_AUTH_SOCK => /not-read','}',
        'environment = {','XPC_SERVICE_NAME => '+LABEL,'}','}']
    return ('\n'.join(rows)+'\n').encode()


def test_measured_loaded_contract_extracts_exact_arguments_and_fields():
    fields=native.loaded_fields(loaded(),ADDRESS)
    assert fields['arguments']==['/selected/python','-I','-B','-c',native.SERVICE_ENTRY,'/fixture/app/brain_supervisor.py']
    assert fields['type']=='LaunchAgent' and fields['domain']==DOMAIN+' [100003]'


@pytest.mark.parametrize('key',['PYTHONPATH','PYTHONHOME','DYLD_INSERT_LIBRARIES','BRAIN_PORT','BASIC_MEMORY_CONFIG_DIR','MCP_CONFIG'])
def test_denied_environment_keys_refuse_without_reading_values(key):
    raw=loaded().replace(b'SSH_AUTH_SOCK => /not-read',(key+' => synthetic').encode())
    with pytest.raises(native.RegistrationError,match='environment'):native.loaded_fields(raw,ADDRESS)


@pytest.mark.parametrize('change',[
    lambda raw:raw.replace(b'type = LaunchAgent',b'type = LaunchAgent\ntype = LaunchAgent'),
    lambda raw:raw.replace(b'arguments = {',b'arguments = invalid'),
    lambda raw:raw[:-2],
    lambda raw:raw+b'trailing data\n',
    lambda raw:raw.replace(ADDRESS.encode(),b'gui/502/'+LABEL.encode(),1),
    lambda raw:raw.replace(b'SSH_AUTH_SOCK =>',b'unknown environment format'),
])
def test_incomplete_or_ambiguous_loaded_grammar_refuses(change):
    with pytest.raises(native.RegistrationError):native.loaded_fields(change(loaded()),ADDRESS)


@pytest.mark.parametrize('present',[True,False])
def test_measured_domain_inventory_requires_closed_block(present):
    row=('123 - '+LABEL+'\n') if present else '0 0 com.apple.fixture\n'
    raw=(DOMAIN+' = {\nservices = {\n'+row+'}\n}\n').encode()
    assert native.domain_contains(raw,DOMAIN,LABEL) is present
    with pytest.raises(native.RegistrationError):native.domain_contains(raw.replace(b'}\n',b''),DOMAIN,LABEL)


@pytest.mark.parametrize('status',['(jt)','(pe)'])
@pytest.mark.parametrize('present',[True,False])
def test_measured_parenthesized_exit_status_rows_are_inventory(status,present):
    # macOS 27.2 prints e.g. "0   (jt)   com.apple.knowledgeconstructiond" for an unrelated agent.
    rows='0 '+status+' com.apple.knowledgeconstructiond\n'+(('123 - '+LABEL+'\n') if present else '')
    raw=(DOMAIN+' = {\nservices = {\n'+rows+'}\n}\n').encode()
    assert native.domain_contains(raw,DOMAIN,LABEL) is present


@pytest.mark.parametrize('row',['- - '+LABEL,'1 0 3 '+LABEL,'1 unknown '+LABEL,'1 - '+LABEL+'\n2 - '+LABEL,
    '1 (jt '+LABEL,'1 () '+LABEL,'1 (JT) '+LABEL,'1 (toolongstatus) '+LABEL,'1 (j t) '+LABEL])
def test_domain_unknown_or_duplicate_rows_refuse(row):
    raw=(DOMAIN+' = {\nservices = {\n'+row+'\n}\n}\n').encode()
    with pytest.raises(native.RegistrationError):native.domain_contains(raw,DOMAIN,LABEL)


@pytest.mark.parametrize('token,enabled',[('disabled',False),('enabled',True)])
def test_measured_persistent_override_state(token,enabled):
    raw=('\ndisabled services = {\n"'+LABEL+'" => '+token+'\n}\n').encode()
    assert native.disabled_state(raw,LABEL) is enabled
    assert native.disabled_state(b'\ndisabled services = {\n}\n',LABEL) is True


@pytest.mark.parametrize('token',['true','false','0','unknown','enabled\n"'+LABEL+'" => enabled'])
def test_unmeasured_disabled_grammar_refuses(token):
    with pytest.raises(native.RegistrationError):
        native.disabled_state(('disabled services = {\n"'+LABEL+'" => '+token+'\n}\n').encode(),LABEL)


def test_definition_authority_is_source_generated_exact_bytes(brain,tmp_path):
    root=tmp_path/'root';root.mkdir();python=tmp_path/'venv/bin/python';python.parent.mkdir(parents=True)
    known=native.definitions(LABEL,root,[str(python)])
    assert len(known)==2
    for payload,argv in known.items():
        value=plistlib.loads(payload)
        assert value['ProgramArguments']==argv
        assert set(value)=={'Label','ProgramArguments','RunAtLoad','KeepAlive','WorkingDirectory','StandardOutPath','StandardErrorPath'}
    assert any(argv[1:3]==['-I','-B'] for argv in known.values())
    isolated=next(argv for argv in known.values() if argv[1:3]==['-I','-B'])
    assert isolated[4] is native.SERVICE_ENTRY
    assert native._text(native.SERVICE_ENTRY)==native.SERVICE_ENTRY
    assert not any(token in native.SERVICE_ENTRY for token in (' = ',' => ','{','}'))


@pytest.mark.parametrize('argument',['relative/python',' /bin/python','/unsafe/{python}','/unsafe/python\n'])
def test_ambiguous_definition_arguments_refuse(brain,tmp_path,argument):
    with pytest.raises(native.RegistrationError):native.definitions(LABEL,tmp_path,[argument])


def fake_registration(monkeypatch,states):
    registration=object.__new__(native.MacRegistration)
    registration.domain=DOMAIN;registration.label=LABEL;registration.path=Path('/fixture.plist')
    rows=iter(states);registration.inspect=lambda:next(rows)
    calls=[];monkeypatch.setattr(native,'command',lambda args,**kw:calls.append(args) or (0,b'',b''))
    monkeypatch.setattr(native.time,'sleep',lambda _:None)
    return registration,calls


def test_stop_requires_persistent_disable_before_manager_end(monkeypatch):
    registration,calls=fake_registration(monkeypatch,[{'enabled':True,'loaded':True}])
    with pytest.raises(native.RegistrationError,match='Disable'):registration.native_stop()
    assert calls==[]


def test_stop_uses_only_exact_manager_address_and_verifies_absence(monkeypatch):
    registration,calls=fake_registration(monkeypatch,[{'enabled':False,'loaded':True},{'enabled':False,'loaded':False}])
    assert registration.native_stop()=={'enabled':False,'loaded':False,'native_end_exit':0,'native_end_timed_out':False}
    assert calls==[['bootout',ADDRESS]]


def test_autostart_refuses_persistently_disabled_registration(monkeypatch):
    registration,calls=fake_registration(monkeypatch,[{'file_present':True,'enabled':False,'loaded':False}])
    with pytest.raises(native.RegistrationError,match='persistently disabled'):registration.start(explicit=False)
    assert calls==[]


def test_restore_refuses_changed_definition_before_enable(monkeypatch):
    registration,calls=fake_registration(monkeypatch,[{'definition_sha256':'new'}])
    with pytest.raises(native.RegistrationError,match='changed'):registration.restore_activation({'enabled':True,'definition_sha256':'old'})
    assert calls==[]


def test_install_refuses_loaded_job_before_any_file_or_manager_mutation(monkeypatch):
    registration,calls=fake_registration(monkeypatch,[{'loaded':True}]);registration.known={b'exact':[]};registration.installable=b'exact'
    with pytest.raises(native.RegistrationError,match='Stop'):registration.install(b'exact')
    assert calls==[]


def manager_fixture(monkeypatch,*,present=False,file_present=False,enabled=True,raw=None,code=None,error=None):
    monkeypatch.setattr(native,'Path',PurePosixPath)
    registration=object.__new__(native.MacRegistration)
    registration.domain=DOMAIN;registration.label=LABEL;registration.uid=501
    registration.path=PurePosixPath('/Users/fixture/Library/LaunchAgents/'+LABEL+'.plist')
    argv=['/selected/python','-I','-B','-c',native.SERVICE_ENTRY,'/fixture/app/brain_supervisor.py']
    registration.known={b'exact':argv}
    saved=((1,2,5,4),b'exact') if file_present else None
    monkeypatch.setattr(native,'read_definition',lambda *_:saved)
    monkeypatch.setattr(native.time,'sleep',lambda _:None)
    calls=[]
    def run(arguments):
        calls.append(arguments)
        if arguments==['print',DOMAIN]:
            row=('123 - '+LABEL) if present else '0 0 com.apple.fixture'
            return 0,(DOMAIN+' = {\nservices = {\n'+row+'\n}\n}\n').encode(),b''
        if arguments==['print',ADDRESS]:
            return (code if code is not None else 0 if present else 113,
                loaded() if raw is None and present else raw or b'',
                error if error is not None else b'' if present else ('Could not find service "'+LABEL+'" in domain for user gui:501\n').encode())
        if arguments==['print-disabled',DOMAIN]:
            return 0,('disabled services = {\n"'+LABEL+'" => '+('enabled' if enabled else 'disabled')+'\n}\n').encode(),b''
        pytest.fail('No mutations belong in inspect')
    monkeypatch.setattr(native,'command',run)
    return registration,calls


def test_absence_requires_two_both_channel_checks(monkeypatch):
    registration,calls=manager_fixture(monkeypatch)
    assert registration.inspect()=={'file_present':False,'loaded':False,'enabled':True,'running':False,'definition_sha256':None}
    assert calls==[['print',DOMAIN],['print',ADDRESS],['print-disabled',DOMAIN]]*2


@pytest.mark.parametrize('options',[
    {'present':True,'code':113,'raw':b'','error':('Could not find service "'+LABEL+'" in gui').encode()},
    {'code':5,'error':b'Input/output error'},
    {'code':113,'raw':b'contradiction'},
    {'code':113,'error':b'unknown failure'},
    {'present':True,'file_present':False},
])
def test_contradictory_or_unknown_manager_state_refuses(monkeypatch,options):
    registration,_=manager_fixture(monkeypatch,**options)
    with pytest.raises(native.RegistrationError):registration.inspect()


def test_loaded_job_must_match_same_exact_owned_disk_definition(monkeypatch):
    registration,calls=manager_fixture(monkeypatch,present=True,file_present=True,enabled=False)
    value=registration.inspect()
    assert value['loaded'] and not value['running'] and not value['enabled']
    assert value['arguments']==['/selected/python','-I','-B','-c',native.SERVICE_ENTRY,'/fixture/app/brain_supervisor.py']
    assert len(calls)==3


@pytest.mark.parametrize('change',[
    {'type':'LaunchDaemon'},{'program':'/another/python'},{'working directory':'/foreign'},
    {'stdout path':'/foreign'},{'stderr path':'/foreign'},{'domain':'gui/502 [100003]'},
    {'state':'unknown'},{'pid':'123'},
])
def test_loaded_identity_mismatch_refuses(monkeypatch,change):
    registration,_=manager_fixture(monkeypatch,present=True,file_present=True,raw=loaded(**change))
    with pytest.raises(native.RegistrationError):registration.inspect()


def test_running_manager_pid_requires_exact_supervisor_role(monkeypatch):
    import brain_process as processes
    registration,_=manager_fixture(monkeypatch,present=True,file_present=True,raw=loaded(state='running',pid='123'))
    closed=[]
    monkeypatch.setattr(processes,'inspect',lambda pid,required:SimpleNamespace(role='server',close=lambda:closed.append(True)))
    with pytest.raises(native.RegistrationError,match='supervisor'):registration.inspect()
    assert closed==[True]


def test_definition_race_refuses_read_only_inspection(monkeypatch):
    registration,_=manager_fixture(monkeypatch)
    values=iter([None,((1,2,5,4),b'exact')])
    monkeypatch.setattr(native,'read_definition',lambda *_:next(values))
    with pytest.raises(native.RegistrationError,match='changed'):registration.inspect()


def test_bootout_timeout_requires_later_verified_absence(monkeypatch):
    registration,_=fake_registration(monkeypatch,[{'enabled':False,'loaded':True},{'enabled':False,'loaded':False}])
    calls=[]
    def timeout(arguments,**kwargs):
        calls.append((arguments,kwargs));raise native.CommandTimeout('synthetic')
    monkeypatch.setattr(native,'command',timeout)
    value=registration.native_stop()
    assert value['native_end_timed_out'] and value['native_end_exit'] is None
    assert calls==[(['bootout',ADDRESS],{'timeout':30})]


def test_install_cannot_write_recognized_legacy_definition(monkeypatch):
    registration,calls=fake_registration(monkeypatch,[])
    registration.installable=b'isolated';registration.known={b'legacy':[],b'isolated':[]}
    with pytest.raises(native.RegistrationError,match='current isolated'):registration.install(b'legacy')
    assert calls==[]


def test_install_rechecks_loaded_state_after_disabling(monkeypatch):
    prior={'loaded':False,'enabled':True,'definition_sha256':'exact'}
    registration,calls=fake_registration(monkeypatch,[prior])
    registration.installable=b'exact';registration.uid=501
    monkeypatch.setattr(native,'read_definition',lambda *_:((1,2,5,4),b'exact'))
    registration._set_enabled=lambda _:dict(prior,loaded=True)
    with pytest.raises(native.RegistrationError,match='changed'):registration.install(b'exact')
    assert calls==[]


def test_running_exact_supervisor_is_accepted_and_closed(monkeypatch):
    import brain_process as processes
    registration,_=manager_fixture(monkeypatch,present=True,file_present=True,raw=loaded(state='running',pid='123'))
    closed=[]
    monkeypatch.setattr(processes,'inspect',lambda pid,required:SimpleNamespace(role='supervisor',close=lambda:closed.append(True)))
    assert registration.inspect()['running']
    assert closed==[True]


def test_darwin_cached_pid_terminate_is_never_signal_authority(monkeypatch):
    import brain_process as processes
    monkeypatch.setattr(processes,'sys',SimpleNamespace(platform='darwin'))
    owned=processes.Process(123,None,None,1,'server','/selected/python','/fixture/app/brain_server.py',1,'direct')
    monkeypatch.setattr(owned,'alive',lambda:True)
    monkeypatch.setattr(processes.psutil,'Process',lambda *_:pytest.fail('A cached PID cannot authorize a process signal'))
    with pytest.raises(processes.OwnershipError,match='native unit or manager'):owned.terminate()


def test_native_command_launch_failure_is_a_registration_refusal(monkeypatch):
    monkeypatch.setattr(native,'native_tool',lambda:'/known/launchctl')
    def failed(*_args,**_kwargs):raise OSError('synthetic process start denied')
    monkeypatch.setattr(native.subprocess,'Popen',failed)
    with pytest.raises(native.RegistrationError,match='unavailable'):native.command(['print',DOMAIN])


def test_running_process_ownership_refusal_is_wrapped(monkeypatch):
    import brain_process as processes
    registration,_=manager_fixture(monkeypatch,present=True,file_present=True,raw=loaded(state='running',pid='123'))
    def failed(*_args,**_kwargs):raise processes.OwnershipError('synthetic unreadable process')
    monkeypatch.setattr(processes,'inspect',failed)
    with pytest.raises(native.RegistrationError,match='ownership is unavailable'):registration.inspect()


def test_bootout_non_timeout_error_does_not_enter_absence_poll(monkeypatch):
    registration,_=fake_registration(monkeypatch,[{'enabled':False,'loaded':True}])
    def failed(*_args,**_kwargs):raise native.RegistrationError('synthetic native command unavailable')
    monkeypatch.setattr(native,'command',failed)
    with pytest.raises(native.RegistrationError,match='synthetic'):registration.native_stop()


def test_successful_restore_observes_original_enabled_state(monkeypatch):
    registration,calls=fake_registration(monkeypatch,[{'definition_sha256':'exact'},{'enabled':True}])
    assert registration.restore_activation({'definition_sha256':'exact','enabled':True})['enabled']
    assert calls==[['enable',ADDRESS]]


def test_successful_explicit_start_enables_then_bootstraps_then_kickstarts(monkeypatch):
    inactive={'file_present':True,'enabled':False,'loaded':False,'running':False}
    registration,calls=fake_registration(monkeypatch,[inactive,dict(inactive,enabled=True),
        dict(inactive,enabled=True,loaded=True),dict(inactive,enabled=True,loaded=True,running=True)])
    assert registration.start(explicit=True)['running']
    assert calls==[['enable',ADDRESS],['bootstrap',DOMAIN,str(registration.path)],['kickstart',ADDRESS]]


@pytest.mark.skipif(os.name=='nt',reason='Actual POSIX nofollow/owned-file/fsync contract')
@pytest.mark.parametrize('disappear',[False,True])
def test_native_install_cas_success_and_postreplace_disappearance(tmp_path,monkeypatch,disappear):
    registration=object.__new__(native.MacRegistration)
    registration.uid=os.geteuid();registration.domain=DOMAIN;registration.label=LABEL
    registration.home=tmp_path/'home';registration.home.mkdir(mode=0o700)
    registration.directory=registration.home/'Library/LaunchAgents';registration.path=registration.directory/(LABEL+'.plist')
    registration.installable=b'exact generated fixture bytes';registration.known={registration.installable:[]}
    state={'enabled':True};calls=[]
    def inspect():
        before=native.read_definition(registration.path,registration.uid)
        return {'loaded':False,'enabled':state['enabled'],
            'definition_sha256':hashlib.sha256(before[1]).hexdigest() if before else None}
    registration.inspect=inspect
    def command(args,**_kw):
        calls.append(args);state['enabled']=args[0]=='enable';return 0,b'',b''
    monkeypatch.setattr(native,'command',command)
    if disappear:
        actual_replace=os.replace
        def replace(source,destination):actual_replace(source,destination);destination.unlink()
        monkeypatch.setattr(native.os,'replace',replace)
        with pytest.raises(native.RegistrationError,match='replacement was not verified'):
            registration.install(registration.installable)
        assert not state['enabled']
    else:
        assert registration.install(registration.installable)['enabled']
        assert registration.path.read_bytes()==registration.installable
        assert not registration.path.stat().st_mode&0o022
    assert calls[0]==['disable',ADDRESS]
    assert not list(registration.directory.glob('.second-brain-*.tmp'))
