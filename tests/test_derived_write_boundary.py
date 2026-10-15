import pytest
import brain_store as store


@pytest.mark.parametrize('target', [
    '02-Areas/AI-Memory/Wiki/Control.md',
    '02-Areas/AI-Memory/Wiki/Index.md',
    '02-Areas/AI-Memory/Wiki/Projects/example.md',
    '02-Areas/AI-Memory/Wiki/Receipts/example.md',
    '02-Areas/AI-Memory/wiki/Control.md',
    '02-Areas/AI-Memory/WIKI/Projects/example.md',
])
def test_general_update_cannot_replace_compiler_owned_pages(brain, target):
    source = store.capture('derived-boundary', 'Synthetic evidence', 'Supported event.',
                           'test', 'synthetic fixture')['path']
    page = store.safe_path(target)
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text('maintainer_device_id: test-host\n\n# Existing compiler or ownership evidence\n', encoding='utf-8')
    before = page.read_bytes()
    with pytest.raises(ValueError, match='compiler-owned'):
        store.update_current(target, 'Replacement', 'Supported content.', [source],
                             store.digest(before))
    assert page.read_bytes() == before


def test_owner_derived_context_remains_writable(brain):
    source = store.capture('derived-context', 'Synthetic evidence', 'Supported event.',
                           'test', 'synthetic fixture')['path']
    receipt = store.update_current('02-Areas/AI-Memory/Current.md', 'Current',
                                   'Supported summary.', [source])
    assert receipt['status'] == 'saved'
