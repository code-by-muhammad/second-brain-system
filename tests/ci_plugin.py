"""Shard selection, execution ledger and watchdog for guarded CI runs."""

import faulthandler
from json import dumps
import os
from os import _exit, write
from pathlib import Path
import sys
from threading import Lock, Thread, TIMEOUT_MAX
from time import monotonic, sleep, time

import psutil
from psutil import Process
import pytest

import ci_plan

_OUTCOMES = ('error', 'failed', 'xfailed', 'xpassed', 'skipped', 'passed')


class Plugin:
    def __init__(self, config):
        directory = os.environ.get('SB_TEST_CI_DIR')
        if not directory or not (path := Path(directory)).is_dir():
            raise pytest.UsageError('SB_TEST_CI_DIR must name an existing directory.')
        self.mapping = ci_plan.load_map()
        self.shard = os.environ.get('SB_TEST_SHARD')
        if self.shard != 'all' and self.shard not in ci_plan.roster(self.mapping):
            raise pytest.UsageError('SB_TEST_SHARD must be all or a key in ci_shards.json.')
        try:
            self.limit = float(os.environ.get('SB_TEST_NODE_TIMEOUT', ''))
            if not 0 < self.limit < TIMEOUT_MAX - 30:
                raise ValueError
        except ValueError:
            raise pytest.UsageError('SB_TEST_NODE_TIMEOUT must be a positive finite watchdog interval.') from None
        if config.pluginmanager.has_plugin('faulthandler'):
            raise pytest.UsageError('Run with -p no:faulthandler when SB_TEST_CI_DIR is set.')
        self.ledger = (path / 'ledger.jsonl').open('w', encoding='utf-8')
        config.add_cleanup(self.ledger.close)
        # The finish timer needs this descriptor through interpreter shutdown.
        self.fd = os.open(path / 'watchdog.txt', os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        self.pid = os.getpid()
        self.lock = Lock()
        faulthandler.enable(file=self.fd, all_threads=True)
        self._arm('collection', '-', 240)
        Thread(target=self._watch, name='sb-ci-watchdog', daemon=True).start()
        self._record('session', platform=sys.platform, shard=self.shard, python=sys.version.split()[0],
                     pytest=pytest.__version__, map_sha256=ci_plan.map_digest(self.mapping))

    def _record(self, event, **fields):
        self.ledger.write(dumps(dict(event=event, **fields)) + '\n')
        self.ledger.flush()

    def _arm(self, scope, nodeid, limit):
        # Keep both timers and the checkpoint aligned with the claimed deadline.
        with self.lock:
            faulthandler.cancel_dump_traceback_later()
            write(self.fd, f'CHECKPOINT {time():.3f} {scope} {limit} {nodeid}\n'.encode())
            self.deadline = monotonic() + limit, scope, nodeid
            faulthandler.dump_traceback_later(limit + 30, file=self.fd, exit=True)

    def _watch(self):
        while True:
            sleep(1)
            with self.lock:
                deadline, scope, nodeid = self.deadline
                if monotonic() < deadline:
                    continue
                try:
                    write(self.fd, f'TIMEOUT {scope} {nodeid}\n'.encode())
                    faulthandler.dump_traceback(file=self.fd, all_threads=True)
                    # psutil's methods look up Process internally. Restore its
                    # real constructor on this terminal path before cleanup.
                    psutil.Process = Process
                    for child in reversed(Process(self.pid).children(recursive=True)):
                        try:
                            child.kill()
                            write(self.fd, f'KILLED {child.pid}\n'.encode())
                        except BaseException:
                            pass
                finally:
                    _exit(1)

    def pytest_collectreport(self, report):
        if report.skipped:
            self._record('collect_skip', id=report.nodeid, reason=report.longrepr[2])

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config, items):
        self._record('collected', ids=[item.nodeid for item in items])
        if self.shard != 'all':
            files = [item.nodeid.split('::', 1)[0] for item in items]
            owners = ci_plan.assign(self.mapping, files)
            selected, deselected = [], []
            for item, file in zip(items, files):
                (selected if owners[file] == self.shard else deselected).append(item)
            config.hook.pytest_deselected(items=deselected)
            items[:] = selected
        self._record('selected', ids=[item.nodeid for item in items])

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item):
        self.result = None
        started = monotonic()
        self._arm('node', item.nodeid, self.limit)
        if reporter := item.config.pluginmanager.get_plugin('terminalreporter'):
            reporter.ensure_newline()
            reporter.write_line(f'START {item.nodeid}')
            reporter.flush()
        self._record('start', id=item.nodeid)
        try:
            return (yield)
        finally:
            outcome, reason, longrepr = self.result or ('error', 'No test result reported', '')
            self._record('end', id=item.nodeid, outcome=outcome, reason=reason,
                         longrepr=longrepr, seconds=monotonic() - started)
            self._arm('between', '-', 120)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_setup(self, item):
        write(self.fd, f'PHASE setup {item.nodeid}\n'.encode())
        return (yield)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_call(self, item):
        write(self.fd, f'PHASE call {item.nodeid}\n'.encode())
        return (yield)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_teardown(self, item):
        write(self.fd, f'PHASE teardown {item.nodeid}\n'.encode())
        return (yield)

    def pytest_runtest_logreport(self, report):
        reason = getattr(report, 'wasxfail', None)
        if report.failed:
            outcome = 'failed' if report.when == 'call' else 'error'
        elif report.skipped:
            outcome = 'xfailed' if reason is not None else 'skipped'
            reason = report.longrepr[2] if reason is None else reason
        elif report.when == 'call':
            outcome = 'xpassed' if reason is not None else 'passed'
        else:
            return
        if self.result is None or _OUTCOMES.index(outcome) < _OUTCOMES.index(self.result[0]):
            self.result = outcome, reason or '', report.longreprtext[:4000]

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self, exitstatus):
        self._record('finish', exitstatus=int(exitstatus))
        self._arm('finish', '-', 240)
