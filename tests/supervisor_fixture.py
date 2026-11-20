"""Explicit transport for pre-existing synthetic lifecycle tests.

Native containment is tested in child processes, never by assigning pytest itself
to a kill-on-close Job. Existing Socket fixtures provide only a synthetic listener.
"""
def install(monkeypatch,supervisor):
    def identity():
        with supervisor.socket.socket() as socket:
            return {'synthetic':True} if socket.connect_ex(('127.0.0.1',supervisor.cfg.PORT))==0 else None
    monkeypatch.setattr(supervisor.processes,'listener_identity',identity)
    monkeypatch.setattr(supervisor.processes,'validate_environment',lambda **kwargs:None)
    monkeypatch.setattr(supervisor.processes,'enter_supervisor_job',lambda:None)
    monkeypatch.setattr(supervisor.service,'write_heartbeat',lambda status:None)


class ReleasePipe:
    """Stands for the standard-input pipe of a bootstrap the supervisor starts: what was sent before it was closed."""
    def __init__(self):
        self.sent=b'';self.closed=False

    def write(self,data):
        assert not self.closed
        self.sent+=data

    def close(self):
        self.closed=True


def script(command):
    """The script a supervisor child runs, and the arguments after it; interpreter options before it are skipped."""
    position=next(index for index,value in enumerate(command) if index and not str(value).startswith('-'))
    return command[position],command[position+1:]


def bootstrap_pipe(command,kwargs):
    """The supervisor holds a bootstrap, and only a bootstrap, behind a pipe; every other child gets no input."""
    import subprocess
    from pathlib import Path
    if Path(script(command)[0]).name=='brain_index_bootstrap.py':
        # A bootstrap that is never released must leave no compiled files in the application folder.
        assert command[1]=='-B' and kwargs['stdin']==subprocess.PIPE
        return ReleasePipe()
    assert kwargs['stdin']==subprocess.DEVNULL
    return None


def released(process):
    return process.stdin.sent==b'start\n' and process.stdin.closed
