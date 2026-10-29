"""Exercise optional progress/direct-log behaviour in real contained children."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
import brain_settings as cfg
import brain_index_bootstrap as bootstrap

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Native Windows containment hooks.')


def test_tick_cancellation_preserves_direct_log_and_drains_children(brain,tmp_path):
    from brain_windows_process import run
    script=tmp_path/'synthetic_index.py';marker=tmp_path/'leaf.json'
    script.write_text('''import json,os,subprocess,sys,time
from pathlib import Path
if len(sys.argv)>2:
 import psutil
 Path(sys.argv[2]).write_text(json.dumps({'pid':os.getpid(),'created':psutil.Process().create_time()}))
 time.sleep(120)
else:
 print('synthetic stdout progress',flush=True)
 print('synthetic stderr progress',file=sys.stderr,flush=True)
 subprocess.Popen([sys.executable,'-I','-B',__file__,'leaf',str(Path(__file__).with_name('leaf.json'))])
 time.sleep(120)
''',encoding='utf-8')
    count=[0]
    def tick():
        count[0]+=1
        if marker.exists():raise bootstrap.IndexStalled('Synthetic cancellation only.')
    log=tmp_path/'private.log'
    with log.open('ab') as output:
        with pytest.raises(bootstrap.IndexStalled):
            run([sys.executable,'-I','-B',str(script)],stdout=output,stderr=output,
                timeout=15,on_tick=tick,direct_output=True,env=cfg.environment())
    assert count[0]>0
    assert 'synthetic stdout progress' in log.read_text() and 'synthetic stderr progress' in log.read_text()
    import psutil
    value=json.loads(marker.read_text())
    try:survivor=psutil.Process(value['pid']);alive=survivor.is_running() and survivor.create_time()==value['created']
    except psutil.NoSuchProcess:alive=False
    assert not alive


def test_success_is_not_overridden_by_late_tick(brain,tmp_path):
    from brain_windows_process import run
    script=tmp_path/'short_index.py'
    script.write_text("print('synthetic complete',flush=True)\n",encoding='utf-8')
    calls=[0]
    def tick():calls[0]+=1
    log=tmp_path/'private.log'
    with log.open('ab') as output:
        result=run([sys.executable,'-I','-B',str(script)],stdout=output,stderr=output,
                   timeout=10,on_tick=tick,direct_output=True,env=cfg.environment())
    assert result.returncode==0 and 'synthetic complete' in log.read_text()


def test_expired_native_deadline_preserves_private_output(brain,tmp_path):
    from brain_windows_process import run
    script=tmp_path/'slow_index.py';script.write_text("import time\nprint('synthetic before timeout',flush=True)\ntime.sleep(120)\n",encoding='utf-8')
    log=tmp_path/'private.log'
    with log.open('ab') as output:
        with pytest.raises(subprocess.TimeoutExpired):
            run([sys.executable,'-I','-B',str(script)],stdout=output,stderr=output,timeout=1,
                on_tick=lambda:None,direct_output=True,env=cfg.environment())
    assert 'synthetic before timeout' in log.read_text()
