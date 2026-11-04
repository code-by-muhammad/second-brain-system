"""Linux lifetime fault controls; fixtures do not establish native acceptance."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest
import brain_linux_identity as native
import brain_process as processes
import brain_service as service
import brain_settings as cfg

BOOT='12345678-1234-1234-1234-123456789abc'


@pytest.mark.skipif(sys.platform!='linux',reason='Actual Linux /proc and CLOCK_BOOTTIME required.')
def test_real_linux_self_lifetime_is_stable_and_precedes_boot_marker():
    """Read only this test process; no manager, service, clock change or signals."""
    before=native.process(os.getpid())
    boundary=native.marker()
    after=native.process(os.getpid())
    assert before==after and before['pid']==os.getpid()
    assert before['boot_id']==boundary['boot_id']
    assert 0<before['start_ticks']<=boundary['boottime_ticks']


def stat_line(pid=123,ticks=100,state='S',comm='python ) spaced'):
    return (str(pid)+' ('+comm+') '+state+' 7'+' 0'*17+' '+str(ticks)+' 0 0').encode()


def test_proc_stat_uses_field_22_after_complete_comm():
    value=native.parse_stat(stat_line(),123)
    assert value=={'pid':123,'parent_pid':7,'start_ticks':100,'state':'S'}


@pytest.mark.parametrize('payload',[b'',b'123 (x) S 1',stat_line(pid=99),stat_line(ticks=0),stat_line(state='?'),b'x'*4097])
def test_malformed_or_wrong_pid_stat_refused(payload):
    with pytest.raises(native.IdentityError):native.parse_stat(payload,123)


def test_process_uses_same_boot_and_start_ticks(monkeypatch):
    monkeypatch.setattr(native,'boot_id',lambda:BOOT)
    monkeypatch.setattr(native,'_read',lambda *args:stat_line())
    assert native.process(123)=={'pid':123,'start_ticks':100,'boot_id':BOOT}
    monkeypatch.setattr(native,'_read',lambda *args:stat_line(state='Z'))
    assert native.process(123) is None


def test_boot_change_during_inspection_refused(monkeypatch):
    values=iter([BOOT,'00000000-0000-0000-0000-000000000000'])
    monkeypatch.setattr(native,'boot_id',lambda:next(values))
    monkeypatch.setattr(native,'_read',lambda *args:stat_line())
    with pytest.raises(native.IdentityError,match='boot changed'):native.process(123)


def test_boot_relative_marker_does_not_use_wall_clock(monkeypatch):
    monkeypatch.setattr(native,'boot_id',lambda:BOOT)
    monkeypatch.setattr(native.os,'sysconf',lambda key:100,raising=False)
    monkeypatch.setattr(native.time,'CLOCK_BOOTTIME',7,raising=False)
    monkeypatch.setattr(native.time,'clock_gettime_ns',lambda clock:2_345_678_901,raising=False)
    monkeypatch.setattr(native.time,'time_ns',lambda:pytest.fail('wall clock used'))
    assert native.marker()=={'boot_id':BOOT,'boottime_ticks':234}


def test_same_tick_and_different_boot_start_are_not_fresh():
    identity={'boot_id':BOOT,'supervisor_boot_id':BOOT,'start_ticks':102,'supervisor_start_ticks':101}
    assert native.fresh(identity,{'boot_id':BOOT,'boottime_ticks':100})
    assert not native.fresh(identity,{'boot_id':BOOT,'boottime_ticks':101})
    assert not native.fresh(identity,{'boot_id':'another','boottime_ticks':100})
    with pytest.raises(native.IdentityError):native.fresh(identity,{})


def test_linux_alive_ignores_wall_clock_and_refuses_pid_reuse(monkeypatch):
    monkeypatch.setattr(processes.sys,'platform','linux')
    life={'pid':123,'start_ticks':100,'boot_id':BOOT}
    owned=processes.Process(123,None,None,8000,'server','python','script',7,'direct',lifetime=life)
    monkeypatch.setattr(processes,'_linux_lifetime',lambda pid:dict(life))
    monkeypatch.setattr(processes.psutil,'Process',lambda pid:pytest.fail('wall clock process birth used'))
    assert owned.alive() and owned.record()['birth']==8000 and owned.record()['start_ticks']==100
    with pytest.raises(processes.OwnershipError,match='native unit'):owned.terminate()
    monkeypatch.setattr(processes,'_linux_lifetime',lambda pid:{**life,'start_ticks':101})
    assert not owned.alive()


def test_linux_parent_same_tick_order_is_allowed(monkeypatch):
    monkeypatch.setattr(processes.sys,'platform','linux')
    parent=SimpleNamespace(birth=999,lifetime={'boot_id':BOOT,'start_ticks':100})
    child=SimpleNamespace(birth=1,lifetime={'boot_id':BOOT,'start_ticks':100})
    assert processes._parent_precedes(parent,child)


def test_linux_runtime_equality_retains_but_does_not_compare_wall_birth(monkeypatch):
    monkeypatch.setattr(processes.sys,'platform','linux')
    left={'pid':2,'supervisor_pid':1,'birth':5,'supervisor_birth':4,'start_ticks':101,
          'supervisor_start_ticks':100,'boot_id':BOOT,'supervisor_boot_id':BOOT}
    assert processes.same_runtime(left,{**left,'birth':1,'supervisor_birth':9999})
    assert not processes.same_runtime(left,{**left,'start_ticks':102})
    assert not processes.same_runtime(left,{**left,'boot_id':'another'})
    assert not processes.same_runtime(left,{})


def test_linux_listener_records_stable_lifetimes_and_rechecks_after_metadata(brain,monkeypatch):
    monkeypatch.setattr(processes.sys,'platform','linux')
    (cfg.ROOT/'state/service-pids.json').write_text(json.dumps({'supervisor':70}))
    server=SimpleNamespace(pid=80,role='server',birth=1,kind='direct',parents=[],parent_pid=70,
        lifetime={'pid':80,'boot_id':BOOT,'start_ticks':100},alive=lambda:True,close=lambda:None,
        record=lambda:{'pid':80,'birth':1,'boot_id':BOOT,'start_ticks':100})
    supervisor=SimpleNamespace(pid=70,role='supervisor',birth=9999,
        lifetime={'pid':70,'boot_id':BOOT,'start_ticks':100},alive=lambda:True,close=lambda:None)
    monkeypatch.setattr(processes,'inspect',lambda pid,**kwargs:server if pid==80 else supervisor)
    monkeypatch.setattr(processes,'_listeners',lambda:[SimpleNamespace(pid=80,laddr=SimpleNamespace(ip='127.0.0.1',port=cfg.PORT))])
    result=processes.listener_identity()
    assert result['supervisor_start_ticks']==100 and result['supervisor_boot_id']==BOOT
    server.alive=lambda:False
    with pytest.raises(processes.OwnershipError,match='lifetime changed'):processes.listener_identity()


def test_linux_heartbeat_binds_lifetime_and_boot_time(brain,monkeypatch):
    monkeypatch.setattr(service.sys,'platform','linux')
    path=cfg.ROOT/'state/service-heartbeat.json'
    value={'status':'ready','pid':123,'birth':9999,'boot_id':BOOT,'start_ticks':100,'written_boottime_ticks':201}
    path.write_text(json.dumps(value))
    owned={'supervisor_pid':123,'supervisor_birth':1,'supervisor_boot_id':BOOT,'supervisor_start_ticks':100}
    attempt={'boot_id':BOOT,'boottime_ticks':200}
    assert service._heartbeat(owned,999999,False,attempt)=='verified'
    for changed in ({'start_ticks':99},{'boot_id':'another'},{'written_boottime_ticks':199},{'written_boottime_ticks':200}):
        path.write_text(json.dumps({**value,**changed}))
        assert service._heartbeat(owned,0,False,attempt) is None


def test_linux_ready_wait_uses_boot_boundary_with_wall_clock_shift(monkeypatch):
    monkeypatch.setattr(service.sys,'platform','linux')
    owned={'birth':1,'supervisor_birth':1,'boot_id':BOOT,'supervisor_boot_id':BOOT,
           'start_ticks':302,'supervisor_start_ticks':301}
    monkeypatch.setattr(processes,'listener_identity',lambda:owned)
    monkeypatch.setattr(service,'_heartbeat',lambda *args:'verified')
    boundary={'boot_id':BOOT,'boottime_ticks':300}
    assert service._wait_started(900000,800000,False,service.time.monotonic()+1,boundary,boundary)==(owned,'verified')


@pytest.mark.parametrize('version,expected',[('0.1.7','legacy-unavailable'),('0.1.8',None),('0.1.9',None),('0.1.6',None)])
def test_linux_legacy_exemption_only_accepted017(brain,monkeypatch,version,expected):
    monkeypatch.setattr(service.sys,'platform','linux')
    monkeypatch.setattr(cfg,'DEVICE',{'version':version})
    assert service._heartbeat({},1,True,{'boot_id':BOOT,'boottime_ticks':100})==expected


@pytest.mark.parametrize('last',[None,{'pid':123,'start_ticks':101,'boot_id':BOOT}])
def test_linux_exit_mid_metadata_is_absent_but_reuse_refused(brain,monkeypatch,last):
    monkeypatch.setattr(processes.sys,'platform','linux')
    monkeypatch.setattr(processes,'_api',lambda:None)
    values=iter([{'pid':123,'start_ticks':100,'boot_id':BOOT},last])
    monkeypatch.setattr(processes,'_linux_lifetime',lambda pid:next(values))
    exe=processes._path(cfg.PYTHON)
    monkeypatch.setattr(processes,'_launchers',lambda:{exe:exe})
    monkeypatch.setattr(processes,'_bm_paths',lambda:set())
    current=SimpleNamespace(create_time=lambda:123,cmdline=lambda:[str(cfg.PYTHON),str(cfg.ROOT/'app/brain_server.py')],
                            exe=lambda:str(cfg.PYTHON),ppid=lambda:7)
    monkeypatch.setattr(processes.psutil,'Process',lambda pid:current)
    if last is None:assert processes.inspect(123) is None
    else:
        with pytest.raises(processes.OwnershipError,match='lifetime changed'):processes.inspect(123)


@pytest.mark.parametrize('empty,since',[
    ({},None),({'empty_boot_id':'another','empty_boottime_ticks':100},None),
    ({'empty_boot_id':BOOT,'empty_boottime_ticks':201},None),
    ({'empty_boot_id':BOOT,'empty_boottime_ticks':100},999),
])
def test_linux_missing_wrong_boot_or_stricter_since_refuses_before_start(brain,monkeypatch,empty,since):
    monkeypatch.setattr(service.sys,'platform','linux')
    monkeypatch.setattr(service,'_qualified_native_platform',lambda:None)
    monkeypatch.setattr(processes,'validate_environment',lambda:None)
    monkeypatch.setattr(processes,'linux_marker',lambda:{'boot_id':BOOT,'boottime_ticks':200})
    monkeypatch.setattr(processes,'listener_identity',lambda:None)
    monkeypatch.setattr(service,'_match_transaction',lambda hold:{'stage':'stopped','empty_at_filetime':500,**empty})
    monkeypatch.setattr(service,'_start_native',lambda:pytest.fail('native start before valid boundary'))
    with pytest.raises(processes.OwnershipError):service.start(explicit=True,hold_id='a'*32,since=since)


@pytest.mark.parametrize('version,legacy',[('0.1.7',True),('0.1.9',False)])
def test_linux_finish_uses_recorded_start_boundary_and_stable_equality(brain,monkeypatch,version,legacy):
    import brain_cli as cli
    monkeypatch.setattr(service.sys,'platform','linux')
    monkeypatch.setattr(service,'_qualified_native_platform',lambda:None)
    monkeypatch.setattr(processes,'validate_environment',lambda:None)
    monkeypatch.setattr(cfg,'DEVICE',{'version':version})
    hold='b'*32
    record={'hold_id':hold,'stage':'started','empty_at_filetime':1000,'empty_boot_id':BOOT,
        'empty_boottime_ticks':100,'started_at_filetime':2000,'started_boot_id':BOOT,
        'started_boottime_ticks':200,'legacy_heartbeat':legacy}
    monkeypatch.setattr(service,'_match_transaction',lambda hold_id:record)
    (cfg.ROOT/'state/service-transaction.json').write_text(json.dumps(record))
    owned={'pid':80,'birth':1,'start_ticks':152,'boot_id':BOOT,'supervisor_pid':70,
           'supervisor_birth':2,'supervisor_start_ticks':151,'supervisor_boot_id':BOOT}
    calls=[]
    def lookup():
        calls.append(1)
        return {**owned,'birth':len(calls),'supervisor_birth':len(calls)+1}
    monkeypatch.setattr(processes,'listener_identity',lookup)
    monkeypatch.setattr(service,'_linux_registration',lambda:SimpleNamespace(inspect=lambda:{
        'file_present':True,'loaded':True,'enabled':True,'running':True,'main_pid':70,
        'main_lifetime':{'pid':70,'start_ticks':151,'boot_id':BOOT}}))
    if not legacy:
        (cfg.ROOT/'state/service-heartbeat.json').write_text(json.dumps({'status':'ready','pid':70,
            'birth':9999,'start_ticks':151,'boot_id':BOOT,'written_boottime_ticks':201}))
    async def health(*args):return {'status':'running','version':version}
    monkeypatch.setattr(cli,'mcp_call',health)
    result=service.finish_transaction(hold,version,allow_legacy=legacy)
    assert result['status']=='transaction released' and len(calls)==2
    journal=json.loads((cfg.ROOT/'state/service-transactions'/(hold+'.json')).read_text())
    assert journal['heartbeat_verified'] is (not legacy)
    assert journal['empty_boot_id']==BOOT and not (cfg.ROOT/'state/service-transaction.json').exists()


@pytest.mark.parametrize('since',[None,400])
def test_linux_positive_start_binds_exact_stop_receipt(brain,monkeypatch,since):
    monkeypatch.setattr(service.sys,'platform','linux')
    monkeypatch.setattr(service,'_qualified_native_platform',lambda:None)
    monkeypatch.setattr(processes,'validate_environment',lambda:None)
    monkeypatch.setattr(processes,'filetime_now',lambda:500)
    monkeypatch.setattr(processes,'linux_marker',lambda:{'boot_id':BOOT,'boottime_ticks':200})
    hold='c'*32
    path=cfg.ROOT/'state/service-transaction.json'
    path.write_text(json.dumps({'hold_id':hold,'root':str(cfg.ROOT),'stage':'stopped',
        'empty_at_filetime':400,'empty_boot_id':BOOT,'empty_boottime_ticks':100}))
    owned={'pid':80,'start_ticks':152,'boot_id':BOOT,'supervisor_pid':70,
           'supervisor_start_ticks':151,'supervisor_boot_id':BOOT}
    lookups=iter([None,None,owned]);native_calls=[]
    native={'file_present':True,'loaded':True,'enabled':False,'running':True,'main_pid':70,
            'main_lifetime':{'pid':70,'start_ticks':151,'boot_id':BOOT}}
    monkeypatch.setattr(service,'_linux_registration',lambda:SimpleNamespace(inspect=lambda:native))
    monkeypatch.setattr(processes,'listener_identity',lambda:next(lookups))
    monkeypatch.setattr(service,'_start_native',lambda **kwargs:native_calls.append('started'))
    monkeypatch.setattr(service,'_wait_started',lambda *args:(owned,'verified'))
    assert service.start(explicit=True,hold_id=hold,since=since)['status']=='started'
    receipt=json.loads(path.read_text())
    assert native_calls==['started'] and receipt['stage']=='started'
    assert receipt['started_boot_id']==BOOT and receipt['started_boottime_ticks']==200


@pytest.mark.parametrize('outcome',['absent','reused','metadata-error-then-absent'])
def test_linux_descendant_lifetime_recheck(brain,monkeypatch,outcome):
    monkeypatch.setattr(processes.sys,'platform','linux')
    monkeypatch.setattr(processes,'_api',lambda:None)
    first={'pid':124,'start_ticks':101,'boot_id':BOOT}
    values=iter([first,{**first,'start_ticks':102} if outcome=='reused' else None])
    monkeypatch.setattr(processes,'_linux_lifetime',lambda pid:next(values))
    parent=SimpleNamespace(pid=123,birth=1,lifetime={'pid':123,'start_ticks':100,'boot_id':BOOT},alive=lambda:True)
    monkeypatch.setattr(processes.psutil,'process_iter',lambda fields:[SimpleNamespace(pid=124,info={'ppid':123})])
    def created():
        if outcome=='metadata-error-then-absent':raise processes.psutil.AccessDenied(124)
        return 123
    child=SimpleNamespace(create_time=created,ppid=lambda:123,exe=lambda:str(cfg.PYTHON))
    monkeypatch.setattr(processes.psutil,'Process',lambda pid:child)
    if outcome=='reused':
        with pytest.raises(processes.OwnershipError,match='lifetime changed'):processes.descendants([parent])
    else:assert processes.descendants([parent])==[]


@pytest.mark.parametrize('case',['before-h3','different-boot','018-missing-metadata'])
def test_linux_finish_refuses_unproved_lifetime_even_with_legacy(brain,monkeypatch,case):
    import brain_cli as cli
    monkeypatch.setattr(service.sys,'platform','linux')
    monkeypatch.setattr(processes,'validate_environment',lambda:None)
    version='0.1.8' if case=='018-missing-metadata' else '0.1.7'
    monkeypatch.setattr(cfg,'DEVICE',{'version':version})
    hold='d'*32
    record={'hold_id':hold,'stage':'started','empty_at_filetime':1000,'started_at_filetime':2000,
        'empty_boot_id':BOOT,'empty_boottime_ticks':100,'started_boot_id':BOOT,'started_boottime_ticks':200,
        'legacy_heartbeat':True}
    if case=='before-h3':
        for key in ('empty_boot_id','empty_boottime_ticks','started_boot_id','started_boottime_ticks'):record.pop(key)
    monkeypatch.setattr(service,'_match_transaction',lambda hold_id:record)
    path=cfg.ROOT/'state/service-transaction.json';path.write_text(json.dumps(record))
    identity_boot='another' if case=='different-boot' else BOOT
    owned={'pid':80,'birth':1,'start_ticks':152,'boot_id':identity_boot,'supervisor_pid':70,
           'supervisor_birth':2,'supervisor_start_ticks':151,'supervisor_boot_id':identity_boot}
    monkeypatch.setattr(processes,'listener_identity',lambda:owned)
    async def no_health(*args):pytest.fail('credentials used before lifetime/heartbeat proof')
    monkeypatch.setattr(cli,'mcp_call',no_health)
    with pytest.raises(processes.OwnershipError):service.finish_transaction(hold,version,allow_legacy=True)
    assert path.exists()


@pytest.mark.parametrize('platform',['win32','darwin'])
def test_nonlinux_equality_preserves_wall_birth_binding(monkeypatch,platform):
    monkeypatch.setattr(processes.sys,'platform',platform)
    assert not processes.same_runtime({'pid':1,'birth':2},{'pid':1,'birth':3})
    assert processes.same_runtime({'pid':1,'birth':2},{'pid':1,'birth':2})


@pytest.mark.parametrize('version',['0.1.0','0.1.6','0.1.7'])
def test_windows_historical_legacy_tuple_preserved(brain,monkeypatch,version):
    monkeypatch.setattr(service.sys,'platform','win32')
    monkeypatch.setattr(cfg,'DEVICE',{'version':version})
    assert service._heartbeat({},1,True)=='legacy-unavailable'
