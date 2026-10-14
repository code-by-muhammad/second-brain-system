"""Generate per-user OS service definitions with the same Python core."""
import json, os, plistlib, shlex, subprocess, sys
from pathlib import Path
import brain_settings as cfg

LABEL='local.second-brain'
TASK='SecondBrainLocalService'

def definition(platform=None):
    platform=platform or sys.platform
    script=cfg.ROOT/'app/brain_supervisor.py'
    if platform=='win32':
        import xml.etree.ElementTree as ET
        root=ET.Element('Task',xmlns='http://schemas.microsoft.com/windows/2004/02/mit/task',version='1.2')
        trigger=ET.SubElement(ET.SubElement(root,'Triggers'),'LogonTrigger'); ET.SubElement(trigger,'Enabled').text='true'
        principal=ET.SubElement(ET.SubElement(root,'Principals'),'Principal',id='Author')
        ET.SubElement(principal,'LogonType').text='InteractiveToken'; ET.SubElement(principal,'RunLevel').text='LeastPrivilege'
        settings=ET.SubElement(root,'Settings')
        for key,value in [('MultipleInstancesPolicy','IgnoreNew'),('ExecutionTimeLimit','PT0S'),('DisallowStartIfOnBatteries','false'),('StopIfGoingOnBatteries','false'),('StartWhenAvailable','true')]: ET.SubElement(settings,key).text=value
        action=ET.SubElement(ET.SubElement(root,'Actions',Context='Author'),'Exec')
        python=cfg.PYTHON.with_name('pythonw.exe')
        ET.SubElement(action,'Command').text=str(python)
        ET.SubElement(action,'Arguments').text='"'+str(script)+'"'
        ET.SubElement(action,'WorkingDirectory').text=str(cfg.ROOT/'app')
        return ET.tostring(root,encoding='unicode')
    if platform=='darwin':
        return plistlib.dumps({'Label':LABEL,'ProgramArguments':[str(cfg.PYTHON),str(script)],'RunAtLoad':True,'KeepAlive':True,
                              'WorkingDirectory':str(cfg.ROOT/'app'),'StandardOutPath':str(cfg.ROOT/'logs/service-supervisor.log'),
                              'StandardErrorPath':str(cfg.ROOT/'logs/service-supervisor.log')}).decode()
    # systemd quoting is not shell quoting. Escape percent specifiers and backslashes.
    def quote(value): return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
    return '[Unit]\nDescription=Local Second Brain MCP\nAfter=network.target\n\n[Service]\nType=simple\nExecStart='+quote(cfg.PYTHON)+' '+quote(script)+'\nRestart=on-failure\nRestartSec=10\n\n[Install]\nWantedBy=default.target\n'

def install():
    (cfg.ROOT/'logs').mkdir(parents=True,exist_ok=True)
    if sys.platform=='win32':
        target=cfg.STATE/'service-task.xml'; target.write_text(definition(),encoding='utf-16')
        subprocess.run(['schtasks.exe','/Create','/TN',TASK,'/XML',str(target),'/F'],check=True,**cfg.child_options())
    elif sys.platform=='darwin':
        target=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(definition(),encoding='utf-8')
        subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(target)],check=True)
    else:
        target=Path.home()/'.config/systemd/user/second-brain.service'; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(definition(),encoding='utf-8')
        subprocess.run(['systemctl','--user','daemon-reload'],check=True)
        subprocess.run(['systemctl','--user','enable','--now','second-brain.service'],check=True)
    return {'status':'installed','definition':str(target)}

def start():
    if sys.platform=='win32': command=['schtasks.exe','/Run','/TN',TASK]
    elif sys.platform=='darwin': command=['launchctl','kickstart',f'gui/{os.getuid()}/{LABEL}']
    else: command=['systemctl','--user','start','second-brain.service']
    subprocess.run(command,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,**cfg.child_options())

def stop():
    if sys.platform=='win32': command=['schtasks.exe','/End','/TN',TASK]
    elif sys.platform=='darwin': command=['launchctl','bootout',f'gui/{os.getuid()}/{LABEL}']
    else: command=['systemctl','--user','stop','second-brain.service']
    subprocess.run(command,check=True,**cfg.child_options())
    registry=cfg.STATE/'service-pids.json'
    if registry.exists():
        import signal
        pids=[pid for pid in json.loads(registry.read_text()).values() if isinstance(pid,int) and pid!=os.getpid()]
        for pid in pids:
            if os.name=='nt':
                root=str(cfg.ROOT/'app').replace("'","''")
                script=f"$brainProcess = Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; if ($brainProcess -and $brainProcess.CommandLine -and $brainProcess.CommandLine.Contains('{root}')) {{ Stop-Process -Id {pid} -Force }}"
                subprocess.run(['powershell.exe','-NoProfile','-Command',script],check=True,**cfg.child_options())
            else:
                p=subprocess.run(['ps','-p',str(pid),'-o','args='],capture_output=True,text=True)
                if str(cfg.ROOT/'app') in p.stdout:
                    try: os.kill(pid,signal.SIGTERM)
                    except ProcessLookupError: pass
