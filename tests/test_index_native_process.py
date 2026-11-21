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


def test_start_line_reaches_a_real_worker_through_the_job_runner(brain,tmp_path):
    """The gate on Windows: the start line is the runner's standard-input file, read by the base interpreter."""
    from brain_windows_process import run
    import brain_index_worker as worker
    source=Path(worker.__file__).read_text(encoding='utf-8')
    assert source.count(worker.REVIEWED_SHA256)==1
    # A copy that is released and then refuses at its guard: it passes the gate and indexes nothing.
    copy=tmp_path/'brain_index_worker.py';copy.write_text(source.replace(worker.REVIEWED_SHA256,'0'*64),encoding='utf-8')
    log=tmp_path/'private.log'
    with log.open('ab') as output:
        released=run([sys.executable,'-I','-B',str(copy),'reindex','-p','brain'],input=bootstrap.RELEASE,stdout=output,stderr=output,
                     timeout=180,direct_output=True,env=cfg.environment())
        unreleased=run([sys.executable,'-I','-B',str(copy),'reindex','-p','brain'],stdout=output,stderr=output,
                       timeout=60,direct_output=True,env=cfg.environment())
    text=log.read_text()
    assert released.returncode==78 and 'index worker: guard_refused:vector_cleanup_not_reviewed' in text
    assert unreleased.returncode==75 and 'index worker: not_released' in text


def test_bootstrap_runner_passes_the_gate_and_closes_its_scope_at_creation(brain,tmp_path):
    from contextlib import contextmanager
    script=tmp_path/'gated.py'
    script.write_text("import sys\nline=sys.stdin.buffer.readline()\nprint('released' if line==b'start\\n' else 'refused',flush=True)\nsys.exit(0 if line==b'start\\n' else 75)\n",encoding='utf-8')
    events=[]
    @contextmanager
    def admission():
        events.append('entered')
        try:yield
        finally:events.append('left')
    log=tmp_path/'private.log'
    with log.open('ab') as output:
        result=bootstrap._run_reindex([sys.executable,'-I','-B',str(script)],output,time.monotonic()+60,lambda:events.append('tick'),lambda:None,admission=admission)
    assert result.returncode==0 and 'released' in log.read_text()
    assert events[:2]==['entered','left']  # Left right after creation, before the first tick.


@pytest.mark.parametrize('keyword,error',[('deadline',subprocess.TimeoutExpired),('start_by',None)])
def test_expired_deadline_or_start_window_creates_no_process(brain,tmp_path,keyword,error):
    from brain_windows_process import run,StartWindowExpired
    marker=tmp_path/'started';script=tmp_path/'never.py'
    script.write_text("from pathlib import Path\nPath("+repr(str(marker))+").write_text('started')\n",encoding='utf-8')
    started=[]
    with (tmp_path/'private.log').open('ab') as output:
        with pytest.raises(error or StartWindowExpired):
            run([sys.executable,'-I','-B',str(script)],stdout=output,stderr=output,timeout=30,direct_output=True,env=cfg.environment(),
                on_started=lambda:started.append(True),**{keyword:time.monotonic()-1})
    assert started==[] and not marker.exists()
