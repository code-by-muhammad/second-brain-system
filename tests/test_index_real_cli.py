"""Shard regressions through the real CLI, the real index worker, Basic Memory and FastEmbed. One synthetic note.

These are the only tests that load the embedding model. They need its files, which the suite never downloads:

    SB_TEST_MODEL_CACHE=<folder>   a FastEmbed cache that holds bge-small-en-v1.5, i.e. a folder with
                                   models--Qdrant--bge-small-en-v1.5-onnx-Q inside it

An installed runtime's <runtime>/models is such a folder; it is only read, and copied into the test sandbox. To make
one elsewhere, with the project environment and network access, outside pytest:

    python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5',cache_dir='<folder>',threads=1)"

Without the variable, or without the model in that folder, every test that embeds is skipped and says so. Each case
takes about a minute on one core. The two tests of the harness itself use a stand-in worker and need no model.

The CLI is given an overall limit that ends before the harness's own wait does, so a run that is too slow ends through
the CLI's own cleanup. If the harness's wait still expires it sends TERM, which the CLI answers by ending its worker's
process group and reaping it; only after that is anything killed. Either way no worker may be left running.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace
import pytest

REPO=Path(__file__).resolve().parents[1]
MODEL='models--Qdrant--bge-small-en-v1.5-onnx-Q'
VARIABLE='SB_TEST_MODEL_CACHE'
# Read at import: the per-test environment is rebuilt, and a variable the suite does not know would not survive it.
CACHE=os.environ.get(VARIABLE,'')


CLI_SECONDS=600      # --overall-seconds: the CLI's own limit, the first to end a slow run.
HARNESS_SECONDS=780  # Then the harness sends TERM. The CLI ends the worker's group (TERM, two seconds, KILL) and reaps it,
TERM_SECONDS=60      # and has this long to do so before anything is killed from outside.


def _packages():
    try:
        from importlib import metadata
        metadata.version('fastembed');metadata.version('basic-memory')
    except Exception:
        return 'fastembed and basic-memory are not installed in this environment.'
    return ''


def _unavailable():
    if not CACHE:
        return VARIABLE+' is not set. Point it at a FastEmbed cache folder that holds '+MODEL+' (an installed runtime\'s models folder is one) to run the real-CLI shard regressions.'
    if not (Path(CACHE)/MODEL).is_dir():
        return VARIABLE+' names a folder with no '+MODEL+' in it; the real-CLI shard regressions need that model and never download it.'
    return _packages()


pytestmark=[pytest.mark.skipif(os.name=='nt',reason='The Windows runner is qualified by its native job.')]
model=pytest.mark.skipif(bool(_unavailable()),reason=_unavailable() or 'available')
packages=pytest.mark.skipif(bool(_packages()),reason=_packages() or 'available')

# Stands for the worker in the tests of the harness: released, then busy until it is ended. It never finishes by
# itself, so that it is gone proves it was ended, however slowly the machine ran the test.
SLOW_WORKER='''import os, sys, time
from pathlib import Path
assert sys.stdin.buffer.readline()==b"start\\n"
released=Path(os.environ["BRAIN_STATE"])/"worker-released"
released.with_suffix(".part").write_text(str(os.getpid()))
released.with_suffix(".part").replace(released)
while True:time.sleep(3600)
'''
RELEASE_SECONDS=300  # How long the stand-in may take to be released before a test of the harness gives up on it.

# Stands between the test and the unchanged CLI: the committed-state read after a pass fails a set number of times.
FAULT='''import os, runpy, sqlite3, sys
from pathlib import Path
cli=Path(sys.argv.pop(1))
sys.path.insert(0,str(cli.parent))
import brain_index_bootstrap as boot
mode=os.environ['TEST_POST_FAIL']
remaining=float('inf') if mode=='always' else int(mode)
settle=boot._ready_settled;read=boot._ready
def settled(deadline=None,attempts=3):
    if attempts==1:return settle(deadline,attempts)
    def failing(deadline=None):
        global remaining
        if remaining:
            remaining-=1
            raise sqlite3.OperationalError('injected post-pass read failure')
        return read(deadline)
    boot._ready=failing
    try:mark=settle(deadline,attempts)
    finally:boot._ready=read
    # One line per pass that was followed by this read: whether the committed state could be read at all.
    with open(os.environ['TEST_POST_LOG'],'a') as record:record.write('readable\\n' if mark is not None else 'unavailable\\n')
    return mark
boot._ready_settled=settled
runpy.run_path(str(cli),run_name='__main__')
'''


def _body(word,lines):
    return '# Synthetic index fixture\n\n'+''.join(f'- {word} sample {number:03d} describes one independent test fact.\n' for number in range(lines))


@pytest.fixture
def runtime(tmp_path):
    """A runtime as the installer lays it out: the real application files, its settings, the model, an empty index."""
    root=tmp_path/'runtime';vault=tmp_path/'notes';state=root/'state'
    for folder in (state,vault,tmp_path/'cache',tmp_path/'tmp',tmp_path/'home'):folder.mkdir(parents=True)
    shutil.copytree(REPO/'app',root/'app',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    if not _unavailable():shutil.copytree(CACHE,root/'models',symlinks=True)
    settings=runpy.run_path(str(REPO/'install.py'))['retrieval_settings'](root,vault)
    (state/'config.json').write_text(json.dumps(settings),encoding='utf-8')
    (root/'device.json').write_text(json.dumps({'vault':str(vault),'python':sys.executable}),encoding='utf-8')
    (tmp_path/'fault-cli.py').write_text(FAULT,encoding='utf-8')
    environment={name:value for name,value in os.environ.items() if not name.startswith(('BRAIN_','BASIC_MEMORY_','PYTHON')) and name!=VARIABLE}  # The child sees only the copy.
    environment.update(HOME=str(tmp_path/'home'),USERPROFILE=str(tmp_path/'home'),BRAIN_SYSTEM_ROOT=str(root),BRAIN_VAULT=str(vault),
                       BRAIN_STATE=str(state),BASIC_MEMORY_CONFIG_DIR=str(state),XDG_CONFIG_HOME=str(tmp_path/'cache'/'config'),
                       XDG_CACHE_HOME=str(tmp_path/'cache'),HF_HOME=str(tmp_path/'cache'/'hf'),HF_HUB_OFFLINE='1',
                       HF_HUB_DISABLE_TELEMETRY='1',DO_NOT_TRACK='1',TMPDIR=str(tmp_path/'tmp'),TEMP=str(tmp_path/'tmp'),
                       TMP=str(tmp_path/'tmp'),FASTEMBED_CACHE_PATH=str(root/'models'))
    def run(fault=None,overall=CLI_SECONDS,timeout=HARNESS_SECONDS,term=TERM_SECONDS,after=None):
        """One `index retry`. Returns its exit, its output, whether the harness had to end it, and what it left running.
        With `after`, the harness wait starts only once that file exists, so it measures the worker and not the start-up."""
        wrapper=[str(tmp_path/'fault-cli.py')] if fault else []
        process=subprocess.Popen([sys.executable,'-B',*wrapper,str(root/'app'/'brain_cli.py'),'index','retry','--overall-seconds',str(overall),
                                  '--stall-seconds',str(overall-1)],  # The stall limit must be the smaller; it is not what is tested.

                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=tmp_path,
                                 env={**environment,**({'TEST_POST_FAIL':fault,'TEST_POST_LOG':str(tmp_path/'post-pass-reads')} if fault else {})})
        ended=None
        if after is not None:  # The CLI prints one short receipt at its end, so its pipes cannot fill meanwhile.
            until=time.monotonic()+RELEASE_SECONDS
            while not after.exists() and process.poll() is None and time.monotonic()<until:time.sleep(.05)
        try:output,error=process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # TERM first: the CLI's runner ends the worker's own session and reaps it. A kill here would end only
            # the CLI and leave that session running.
            # The allowance is counted in one-second waits, not read off a clock: a machine that stands still for a
            # while and then jumps its clock forward costs one of them, not the whole allowance.
            ended="TERM";process.terminate();output=None
            for _ in range(term):
                try:output,error=process.communicate(timeout=1);break
                except subprocess.TimeoutExpired:pass
            if output is None:ended='KILL';process.kill();output,error=process.communicate()
        left=_left_running(root)
        for pid in left:  # Reported by the caller as a failure; never left behind for the next test either way.
            try:os.kill(pid,signal.SIGKILL)
            except OSError:pass
        return SimpleNamespace(code=process.returncode,output=output,error=error,ended=ended,left=left)
    def retry(fault=None):
        result=run(fault)
        assert result.left==[],'Processes of this runtime outlived the CLI: '+repr(result.left)
        assert result.ended is None,'The CLI passed both its own limit and the harness wait and was ended with '+result.ended
        assert result.output.strip(),'The CLI printed no receipt; exit '+str(result.code)
        return result.code,json.loads(result.output)
    return SimpleRuntime(root,vault/'synthetic.md',retry,run,tmp_path/'post-pass-reads')


def _left_running(root):
    """PIDs of processes started for this runtime that still run: the worker, by its script path, and on Linux anything
    that inherited this runtime's environment. A zombie has neither a command line nor an environment to read."""
    worker=str(root/'app'/'brain_index_worker.py').encode();marker=str(root).encode();found=[]
    if Path('/proc/self/cmdline').exists():
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit() or int(entry.name)==os.getpid():continue
            try:command=(entry/'cmdline').read_bytes()
            except OSError:continue
            try:inherited=(entry/'environ').read_bytes()
            except OSError:inherited=b''
            if worker in command or b'BRAIN_SYSTEM_ROOT='+marker+b'\0' in inherited+b'\0':found.append(int(entry.name))
    else:
        listing=subprocess.run(['ps','-axww','-o','pid=,command='],stdout=subprocess.PIPE,timeout=30).stdout
        found=[int(line.split()[0]) for line in listing.splitlines() if worker in line]
    return sorted(found)


class SimpleRuntime:
    def __init__(self,root,note,retry,run,reads):self.root=root;self.note=note;self.retry=retry;self.run=run;self._reads=reads
    def vectors(self):
        with closing(sqlite3.connect(self.root/'state'/'memory.db')) as connection:
            return connection.execute('SELECT count(*) FROM search_vector_embeddings_rowids').fetchone()[0]
    def compiled(self):return [path for path in (self.root/'app').rglob('*') if path.name=='__pycache__' or path.suffix=='.pyc']
    def post_pass_reads(self):return self._reads.read_text().split() if self._reads.exists() else []
    def slow_worker(self):
        (self.root/'app'/'brain_index_worker.py').write_text(SLOW_WORKER,encoding='utf-8')
        return self.root/'state'/'worker-released'


def _first_index(runtime):
    """640 lines are 641 chunks, embedded 256 per pass: three passes."""
    runtime.note.write_text(_body('Quartz',640),encoding='utf-8')
    code,first=runtime.retry()
    assert (code,first['status'],first['process_exit'],first['passes'])==(0,'verified',0,3),first.get('reason')
    assert first['current_index']['vectors']['chunks']==641 and runtime.vectors()==641
    return first


def _gone(pid,seconds=10):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        try:os.kill(pid,0)
        except ProcessLookupError:return True
        except OSError:pass
        time.sleep(.1)
    return False


@packages
def test_a_cli_that_outlasts_the_harness_wait_is_sent_term_and_reaps_its_worker(runtime):
    """The worker runs in its own session. Killing the CLI at the harness's timeout left it running.

    The stand-in never ends by itself and the wait starts at its release, so no timing decides the outcome: the
    worker is certainly running when TERM is sent, and it can only be gone afterwards because the CLI ended it."""
    released=runtime.slow_worker()
    runtime.note.write_text(_body('Quartz',2),encoding='utf-8')
    result=runtime.run(timeout=2,term=TERM_SECONDS,after=released)
    assert released.exists(),"The worker was never released, so nothing was proved. "+repr((result.code,result.output[-600:],result.error[-1500:]))
    worker=int(released.read_text())
    assert result.ended=='TERM' and result.left==[]  # The CLI ended on TERM alone, and nothing of this runtime runs.
    assert _gone(worker)
    assert json.loads((runtime.root/'state'/'index-bootstrap-status.json').read_text())['status']!='indexing'


@packages
def test_the_cli_limit_ends_a_slow_run_before_the_harness_has_to(runtime):
    released=runtime.slow_worker()
    runtime.note.write_text(_body('Quartz',2),encoding='utf-8')
    result=runtime.run(overall=4,timeout=HARNESS_SECONDS)
    assert released.exists(),repr((result.code,result.output[-600:],result.error[-1500:]))
    assert result.ended is None and result.left==[]
    assert _gone(int(released.read_text()))
    receipt=json.loads(result.output)
    # The runner's own deadline ended the pass: recorded as a failed run that names the timeout.
    assert (receipt['status'],receipt['reason'],receipt.get('error_type'))==('needs attention','local_indexing_failed','TimeoutExpired'),receipt


# The same lines with another word: every chunk key is kept and every chunk is embedded again in place, so no row
# count moves between passes. More lines: the note grows past what one pass embeds. Either way the loop must go on
# until coverage verifies, and must stop when it only cannot read what was committed.
@pytest.mark.parametrize('lines,fault,status,passes,chunks',[
    (640,None,'verified',3,641),      # same keys
    (640,'1','verified',3,641),       # same keys, one failed read of the committed state after a pass
    (640,'3','verified',3,641),       # same keys, all three reads after the first pass fail; the next pass's read works
    (1200,None,'verified',5,1201),    # growth
    (1200,'always','needs review',3,None),  # growth, the committed state never readable: two follow-ups, then stop
],ids=['same-key','same-key-one-failed-read','same-key-three-failed-reads','growth','growth-state-never-readable'])
@model
def test_an_edited_large_note_is_indexed_through_the_real_cli(runtime,lines,fault,status,passes,chunks):
    _first_index(runtime)
    runtime.note.write_text(_body('Jasper',lines),encoding='utf-8')
    written=hashlib.sha256(runtime.note.read_bytes()).hexdigest()
    code,final=runtime.retry(fault)
    assert (code,final['status'],final['passes'],final['process_exit'])==(0,status,passes,0),final.get('reason')
    assert hashlib.sha256(runtime.note.read_bytes()).hexdigest()==written  # Indexing never writes the note.
    if status=='verified':
        assert final['inventory_unchanged_during_run'] is True
        vectors=final['current_index']['vectors']
        assert (vectors['chunks'],vectors['invalid'])==(chunks,0) and runtime.vectors()==chunks
    else:
        assert final['current_index']['vectors']['invalid']>=1
    # Recovery from a state that could not be read: unknown after pass one earned the follow-up, and the state
    # pass two committed was read and counted as progress. Pass three verified, so nothing was read after it.
    if fault=='3':assert runtime.post_pass_reads()==['unavailable','readable']
    if fault=='1':assert runtime.post_pass_reads()==['readable','readable']
    if fault=='always':assert runtime.post_pass_reads()==['unavailable']*3
    assert runtime.compiled()==[]  # The worker runs with -I -B; the CLI was started with -B.
