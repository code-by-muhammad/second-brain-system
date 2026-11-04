"""Actual upstream ignore semantics and read-only coverage of derived history."""
from contextlib import closing
import json
from pathlib import Path
import pytest
import brain_index as index
import brain_settings as cfg
from test_index_coverage import config,database,add_note

def test_migration_preserves_owner_bytes_and_pinned_upstream_scope(brain,monkeypatch):
    from basic_memory.ignore_utils import should_ignore_path,load_gitignore_patterns
    vault,state=brain;monkeypatch.setenv('BASIC_MEMORY_CONFIG_DIR',str(state));config();old=b'# Owner patterns\r\nPrivateScratch/\r\n';(state/'.bmignore').write_bytes(old)
    receipt=index.ensure_history_scope()
    assert receipt['status']=='updated' and (state/'.bmignore').read_bytes().startswith(old)
    assert Path(receipt['backup']).read_bytes()==old
    after=(state/'.bmignore').read_bytes();assert index.ensure_history_scope()['status']=='unchanged' and (state/'.bmignore').read_bytes()==after
    patterns=load_gitignore_patterns(vault)
    archive='02-Areas/AI-Memory/Wiki/Log-Archives/'+'a'*64+'.md'
    assert should_ignore_path(vault/archive,vault,patterns)
    for owner in ('01-Projects/Log-Archives/Owner.md','02-Areas/AI-Memory/Wiki/Log-Archives/history.md','01-Projects/'+'a'*64+'.md'):
        assert not should_ignore_path(vault/owner,vault,patterns)
    with closing(database()) as conn:
        add_note(conn,1,'01-Projects/Source.md')
        # This formerly indexed row remains physically present. The receipt
        # counts it outside current scope; it never claims search erasure.
        add_note(conn,2,archive)
    path=vault/archive;path.write_bytes(b'A'*2_000_001)
    db_before=(state/'memory.db').read_bytes();report=index.status()
    assert report['status']=='verified' and report['coverage']['eligible']==1 and report['coverage']['extra']==0
    assert report['coverage']['generated_history_files']==1 and report['coverage']['excluded_derived_history_entities']==1
    assert 'remain searchable' in report['manifest_scope'] and path.stat().st_size==2_000_001
    assert (state/'memory.db').read_bytes()==db_before

@pytest.mark.parametrize('original',[None,b'',b'# Comment-only owner file\n'])
def test_absent_or_empty_ignore_keeps_upstream_defaults(brain,monkeypatch,original):
    from basic_memory.ignore_utils import DEFAULT_IGNORE_PATTERNS,load_gitignore_patterns
    vault,state=brain;monkeypatch.setenv('BASIC_MEMORY_CONFIG_DIR',str(state))
    if original is not None:(state/'.bmignore').write_bytes(original)
    index.ensure_history_scope()
    assert DEFAULT_IGNORE_PATTERNS<=load_gitignore_patterns(vault)

def test_scope_migration_refuses_incomplete_install_and_concurrent_owner_edit(brain,monkeypatch):
    _,state=brain;target=state/'.bmignore';target.write_bytes(b'Owner/\n')
    transaction=state/'install-transaction.json';transaction.write_text('{"stage":"deployed"}')
    with pytest.raises(index.IndexAttention,match='initial_install_incomplete'):index.ensure_history_scope()
    assert target.read_bytes()==b'Owner/\n';transaction.unlink()
    native=index.os.fsync;fired=[]
    def fsync(fd):
        native(fd)
        if not fired:fired.append(True);target.write_bytes(b'Later owner/\n')
    monkeypatch.setattr(index.os,'fsync',fsync)
    with pytest.raises(index.IndexAttention,match='ignore_changed'):index.ensure_history_scope()
    assert target.read_bytes()==b'Later owner/\n'

def test_invalid_note_encoding_is_explicit_and_original_is_unchanged(brain):
    vault,state=brain;config();source=vault/'Original.md';source.write_bytes(b'\xffOriginal bytes')
    assert index.status()['reason']=='note_encoding_invalid'
    assert source.read_bytes()==b'\xffOriginal bytes' and not (state/'memory.db').exists()
