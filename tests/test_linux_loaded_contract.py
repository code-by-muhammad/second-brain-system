"""OS-boundary fault controls only; never native Linux qualification."""
from copy import deepcopy
from pathlib import Path

import pytest

from brain_linux_registration import RegistrationError, loaded_contract


UNIT='second-brain-ci-'+'a'*32+'.service'
PATH=Path.cwd()/'fixture/second-brain.service'
ARGV=(str(Path.cwd()/'fixture/python'),str(Path.cwd()/'fixture/brain_supervisor.py'))
PREFIX='/user.slice/user-1001.slice/user@1001.service'


def property_(signature,data):
    return {'type':signature,'data':data}


def known():
    u={key:property_('s',value) for key,value in {
        'Id':UNIT,'Following':'','FragmentPath':str(PATH),'SourcePath':'','LoadState':'loaded',
        'UnitFileState':'enabled','ActiveState':'active','SubState':'running'}.items()}
    u.update({key:property_('as',data) for key,data in {'Names':[UNIT],'DropInPaths':[],
        'TriggeredBy':[],'UpheldBy':[],'BoundBy':[],'RequiredBy':[],'OnFailureOf':[],
        'OnSuccessOf':[],'WantedBy':['default.target']}.items()})
    u.update({key:property_('as',[]) for key in ('Requisite','Wants','BindsTo','PartOf','Upholds',
        'OnFailure','OnSuccess','Triggers','PropagatesStopTo')})
    u['Requires']=property_('as',['app.slice']);u['Conflicts']=property_('as',['shutdown.target'])
    u.update(Transient=property_('b',False),NeedDaemonReload=property_('b',False),
             InvocationID=property_('ay',[1]*16),Job=property_('(uo)',[0,'/']))
    s={key:property_('s',value) for key,value in {'Type':'simple','Restart':'on-failure',
        'KillMode':'control-group','Slice':'app.slice','ControlGroup':PREFIX+'/app.slice/'+UNIT,
        'WorkingDirectory':'','RootDirectory':'','RootImage':'','User':'','Group':'','PAMName':''}.items()}
    s.update(KillSignal=property_('i',15),SendSIGKILL=property_('b',True),
        RestartUSec=property_('t',10_000_000),DynamicUser=property_('b',False),
        TimeoutStopUSec=property_('t',90_000_000),MainPID=property_('u',1234),ControlPID=property_('u',0),
        ExecStart=property_('a(sasbttttuii)',[[ARGV[0],list(ARGV),False,0,0,0,0,1234,0,0]]))
    s.update({key:property_('a(sasbttttuii)',[]) for key in
        ('ExecStartPre','ExecStartPost','ExecCondition','ExecReload','ExecStop','ExecStopPost')})
    s.update({key:property_('as',[]) for key in ('Environment','PassEnvironment','UnsetEnvironment','ExecSearchPath')})
    s['EnvironmentFiles']=property_('a(sb)',[])
    return u,s


def inspect(u,s):
    return loaded_contract(UNIT,PATH,ARGV,PREFIX,u,s)


def test_exact_loaded_contract_normalizes_without_io():
    state=inspect(*known())
    assert state['enabled'] is True and state['running'] is True
    assert state['main_pid']==1234 and state['loaded'] and state['file_present']


def test_disabled_inactive_is_preserved():
    u,s=known()
    u['UnitFileState']['data']='disabled';u['ActiveState']['data']='inactive'
    u['SubState']['data']='dead';s['ControlGroup']['data']='';s['MainPID']['data']=0
    state=inspect(u,s)
    assert state['enabled'] is False and state['running'] is False


@pytest.mark.parametrize('side,key,data',[
    ('u','Id','foreign.service'),('u','Names',[UNIT,'alias.service']),('u','Following','foreign'),
    ('u','FragmentPath','C:/other/unit'),('u','SourcePath','foreign'),('u','DropInPaths',['foreign']),
    ('u','LoadState','masked'),('u','Transient',True),('u','NeedDaemonReload',True),
    ('u','UnitFileState','enabled-runtime'),('u','TriggeredBy',['timer']),('u','WantedBy',['foreign.target']),
    ('u','InvocationID',[]),('u','Job',[1,'/']),('u','ActiveState','unknown'),
    ('u','Wants',['foreign.service']),('u','Requires',['foreign.target']),('u','Conflicts',['foreign.service']),
    ('s','Type','forking'),('s','Restart','always'),('s','KillMode','process'),('s','KillSignal',9),
    ('s','SendSIGKILL',False),('s','RestartUSec',1),('s','DynamicUser',True),('s','Slice','foreign.slice'),
    ('s','ExecStop',[['foreign']]),('s','Environment',['LD_PRELOAD=x']),('s','EnvironmentFiles',[['x',False]]),
    ('s','WorkingDirectory','foreign'),('s','User','root'),('s','TimeoutStopUSec',2**64-1),
    ('s','ControlGroup',PREFIX+'/app.slice/foreign.service'),('s','MainPID',-1),('s','ExecStart',[]),
])
def test_refuses_foreign_or_uncertain_loaded_state(side,key,data):
    u,s=known();({'u':u,'s':s}[side])[key]['data']=deepcopy(data)
    with pytest.raises(RegistrationError):inspect(u,s)


def test_missing_or_wrong_typed_property_refused():
    u,s=known();del u['DropInPaths']
    with pytest.raises(RegistrationError):inspect(u,s)
    u,s=known();s['ExecStart']['type']='s'
    with pytest.raises(RegistrationError):inspect(u,s)


@pytest.mark.parametrize('extended',[[[ARGV[0],list(ARGV),['ignore-failure'],0,0,0,0,0,0,0]], [None]])
def test_command_prefixes_or_malformed_extension_refused(extended):
    u,s=known();s['ExecStartEx']=property_('a(sasasttttuii)',extended)
    with pytest.raises(RegistrationError):inspect(u,s)
