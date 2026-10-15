"""Real Python pipes with synthetic data; no owner state, service or model access."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[1]
PATHS = ['01-Projects/Caf\u00e9.md', '01-Projects/\u0627\u0631\u062f\u0648.md', '01-Projects/\U0001f600.md']


def updater_module():
    spec = importlib.util.spec_from_file_location('encoding_update', REPO/'update.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pipe_environment(tmp_path, monkeypatch):
    root = tmp_path/'runtime'; app = root/'app'; app.mkdir(parents=True)
    vault = tmp_path/'vault'; vault.mkdir()
    monkeypatch.setenv('PYTHONIOENCODING', 'cp1252:strict')
    monkeypatch.setenv('PYTHONUTF8', '0')
    monkeypatch.setenv('BRAIN_TEST_MARKER', 'inherited-synthetic-value')
    # Backends are supplied by each test; the actual subprocess API is never mocked.
    return root, app, vault


@pytest.mark.parametrize('source', PATHS, ids=['accent', 'urdu', 'emoji'])
def test_cli_json_is_utf8_even_when_parent_requests_cp1252(pipe_environment, monkeypatch, source):
    root, app, vault = pipe_environment
    shutil.copyfile(REPO/'app/brain_cli.py', app/'brain_cli.py')
    (app/'brain_settings.py').write_text('# isolated synthetic backend\n', encoding='utf-8')
    (app/'brain_wiki.py').write_text(
        'import json, os\n'
        'def migrate_legacy_receipts(**kwargs):\n'
        '    return {"path": json.loads(os.environ["BRAIN_TEST_PATH"]), **kwargs}\n',
        encoding='utf-8')
    monkeypatch.setenv('BRAIN_TEST_PATH', json.dumps(source))
    child = subprocess.run([sys.executable, str(app/'brain_cli.py'), 'wiki', 'migrate', '--apply'],
                           capture_output=True, timeout=10)
    assert child.returncode == 0, child.stderr.decode('utf-8', errors='backslashreplace')
    result = json.loads(child.stdout.decode('utf-8'))
    assert result == {'path': source, 'requeue_unverified': False, 'dry_run': False}
    assert source.encode('utf-8') in child.stdout


@pytest.mark.parametrize('command', ['wiki', 'health'])
@pytest.mark.parametrize('source', PATHS, ids=['accent', 'urdu', 'emoji'])
def test_updater_children_use_utf8_for_both_output_streams(pipe_environment, monkeypatch, command, source):
    root, app, vault = pipe_environment
    # This child deliberately leaves its streams untouched, proving the updater
    # supplies UTF-8 before Python starts, including for an older rollback CLI.
    (app/'brain_cli.py').write_text(
        'import json, os, sys\n'
        'source = json.loads(os.environ["BRAIN_TEST_PATH"])\n'
        'result = {"path": source, "status": "running", "version": "0.1.6",\n'
        '          "stdout_encoding": sys.stdout.encoding, "stderr_encoding": sys.stderr.encoding,\n'
        '          "marker": os.environ["BRAIN_TEST_MARKER"]}\n'
        'if sys.argv[1] == "wiki":\n'
        '    result.update(root=os.environ["BRAIN_SYSTEM_ROOT"], state=os.environ["BRAIN_STATE"],\n'
        '                  vault=os.environ["BRAIN_VAULT"], action=sys.argv[2], flags=sys.argv[3:])\n'
        'sys.stderr.write(source)\n'
        'print(json.dumps(result, ensure_ascii=False))\n', encoding='utf-8')
    monkeypatch.setenv('BRAIN_TEST_PATH', json.dumps(source))
    updater = updater_module()
    if command == 'wiki':
        result = updater.wiki_command(Path(sys.executable), root, vault, 'migrate', '--apply')
        assert (result['root'], result['state'], result['vault']) == (str(root), str(root/'state'), str(vault))
        assert result['action'] == 'migrate' and result['flags'] == ['--apply']
    else:
        monkeypatch.setattr(updater.time, 'sleep', lambda delay: None)
        result = updater.wait_health(Path(sys.executable), root, '0.1.6', timeout=0.5)
    assert result['path'] == source
    assert result['stdout_encoding'].lower().replace('_', '-') == 'utf-8'
    assert result['stderr_encoding'].lower().replace('_', '-') == 'utf-8'
    assert result['marker'] == 'inherited-synthetic-value'
    assert os.environ['PYTHONIOENCODING'] == 'cp1252:strict'
