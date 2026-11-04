"""Existing initial journals refuse before provisioning and preserve planned Python."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest


class ProvisionBoundaryReached(RuntimeError):pass


@pytest.fixture
def pending(tmp_path,monkeypatch):
    source=Path(__file__).resolve().parents[1]/'install.py'
    spec=importlib.util.spec_from_file_location('pending_initial_control',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    root=tmp_path/'runtime';state=root/'state';state.mkdir(parents=True)
    vault=tmp_path/'vault';vault.mkdir()
    checkout=tmp_path/'checkout';planned=checkout/'.venv/Scripts/python.exe';planned.parent.mkdir(parents=True)
    planned.write_bytes(b'Synthetic planned interpreter marker')
    marker=planned.parent.parent/'keep.txt';marker.write_bytes(b'Synthetic environment marker')
    calls=[]
    def provision(*args):calls.append(args);raise ProvisionBoundaryReached('synthetic boundary; nothing provisioned')
    support=SimpleNamespace(host_context=lambda:None,runtime_paths=lambda path:None,provision=provision)
    monkeypatch.setattr(module,'_qualified_platform',lambda:None)
    monkeypatch.setattr(module,'setup_helpers',lambda:support)
    monkeypatch.setattr(module,'REPO',checkout)
    monkeypatch.setattr(sys,'argv',['install.py','--root',str(root),'--vault',str(vault)])
    return module,root,vault,planned,marker,calls


@pytest.mark.parametrize('stage',['prepared','deploying','malformed'])
def test_existing_initial_journal_refuses_before_provision_without_resume(pending,stage):
    module,root,vault,planned,marker,calls=pending
    journal=root/'state/install-transaction.json'
    journal.write_text(json.dumps({'format':1,'install_id':'a'*32,'stage':stage,
        'identity':{'root':str(root),'vault':str(vault),'python':str(planned)}}))
    if stage=='malformed':journal.write_bytes(b'synthetic malformed journal')
    before=[path.read_bytes() for path in (journal,planned,marker)]
    with pytest.raises(ValueError,match='journal'):module.main()
    assert calls==[] and [path.read_bytes() for path in (journal,planned,marker)]==before


def test_missing_initial_journal_preserves_the_normal_provisioning_route(pending):
    module,root,vault,planned,marker,calls=pending
    before=[path.read_bytes() for path in (planned,marker)]
    with pytest.raises(ProvisionBoundaryReached):module.main()
    assert len(calls)==1 and [path.read_bytes() for path in (planned,marker)]==before
