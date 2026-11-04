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
