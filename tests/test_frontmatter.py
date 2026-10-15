import pytest
from brain_markdown import frontmatter
from test_wiki import fixture
import brain_store as s
import brain_wiki as w


@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
def test_quoted_and_indented_fences_preserve_metadata(newline):
    text = '---\ntitle: "Before --- after"\nquote: |\n  Evidence --- inline\n  ---\nwiki_kind: Concepts\n---\n# Body\n---\n'
    parsed = frontmatter('\ufeff' + text.replace('\n', newline), strict=True)
    assert parsed['wiki_kind'] == 'Concepts'
    assert parsed['quote'] == 'Evidence --- inline\n---\n'


@pytest.mark.parametrize('text', [
    '---\ntitle: unfinished\n',
    '---\ntitle: [broken\n---\n',
    '---\n- list metadata\n---\n',
    '---\nscalar metadata\n---\n',
])
def test_invalid_metadata_is_safe_and_can_be_reported(text):
    assert frontmatter(text) == {}
    with pytest.raises(ValueError):
        frontmatter(text, strict=True)


def test_inline_fence_evidence_remains_indexed_and_linked(brain):
    source, _, result = fixture(brain)
    quote = 'A prototype --- with production still planned.'
    source.write_text('---\ndate: 2026-10-01\n---\n# Example\n' + quote, encoding='utf-8')
    packet = w.prepare()
    result['pages'][0]['claims'][0]['quote'] = quote
    second = {**result['pages'][0], 'slug': 'related', 'related': [w.BASE + 'Projects/example.md']}
    result['pages'].append(second)
    originals = source.read_bytes()
    w.apply(packet, result)
    assert len(w.pages()) == 2
    assert not w.lint()['issues']
    index = (s.VAULT / w.BASE / 'Index.md').read_text(encoding='utf-8')
    assert 'Projects/example' in index and 'Projects/related' in index
    assert source.read_bytes() == originals


def test_capture_dedup_with_inline_fence_in_metadata(brain):
    args = ('fence-event', 'Title', 'Milestone.', 'tests', 'fixture --- reference')
    first = s.capture(*args)
    assert s.capture(*args)['status'] == 'duplicate'
    assert s.read(first['path'])['sha256'] == first['sha256']


def test_lint_reports_invalid_topic_instead_of_silently_omitting_it(brain):
    topic = s.VAULT / w.BASE / 'Concepts/broken.md'
    topic.parent.mkdir(parents=True)
    topic.write_text('---\nwiki_kind: [broken\n---\n# Topic', encoding='utf-8')
    assert w.lint()['issues'] == [{'page': w.BASE + 'Concepts/broken.md',
                                  'issue': 'wiki metadata missing or invalid', 'field': 'frontmatter'}]
