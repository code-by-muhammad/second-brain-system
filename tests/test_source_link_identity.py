"""Exact human source references through the four real public write routes.

Exceptional source names use code display under the conservative R143 policy.
Only the literal-# client failure is independently observed. These serialization
regressions do not claim an actual Obsidian renderer/cache/click execution. The
old URI-contract module and receipts remain immutable in the prior R143 clone.
"""
import hashlib
import json

import pytest
import brain_store as s
import brain_wiki as w
from brain_markdown import frontmatter


ROUTES=['capture','update','wiki-evidence','wiki-source']
SPECIAL_CASES=[
    ('03-Resources/Spec#Plan.md','Source path: `03-Resources/Spec#Plan.md`'),
    ('03-Resources/Spec]]Other.md','Source path: `03-Resources/Spec]]Other.md`'),
    ('03-Resources/Spec^Plan.md','Source path: `03-Resources/Spec^Plan.md`'),
    ('03-Resources/100%2F (draft) [α]#β.md','Source path: `03-Resources/100%2F (draft) [α]#β.md`'),
    ('03-Resources/Folder#one/Source.MD','Source path: `03-Resources/Folder#one/Source.MD`'),
    ('03-Resources/Spec``#Plan.md','Source path: ```03-Resources/Spec``#Plan.md```'),
    ('03-Resources/`Spec#Plan.md','Source path: ``03-Resources/`Spec#Plan.md``'),
]
SPECIAL_IDS=['hash','closing-brackets','caret','literal-percent-unicode-parentheses',
             'folder-hash-uppercase','backtick-run','basename-prefix-backtick']
QUOTE='A synthetic prototype was demonstrated; production remains planned.'


def _publish(brain,route,relative):
    vault,_=brain
    source=vault/relative;source.parent.mkdir(parents=True,exist_ok=True)
    original=('---\ndate: 2026-10-01\n---\n# Synthetic source\n\n'+QUOTE+'\n').encode('utf-8')
    source.write_bytes(original);source_sha=hashlib.sha256(original).hexdigest()
    if route=='capture':
        arguments=dict(event_id='r143-literal-source',title='Synthetic link milestone',
                       outcome='A source-backed synthetic milestone.',source_app='tests',
                       source_reference='synthetic source-link contract',evidence_paths=[relative])
        receipt=s.capture(**arguments);page=receipt['path'];text=(vault/page).read_text(encoding='utf-8')
        assert s.capture(**arguments)['status']=='duplicate'
        assert (vault/page).read_text(encoding='utf-8')==text
        link=text.split('## Evidence\n',1)[1].splitlines()[0].removeprefix('- ')
        payload=dict(arguments,project='',decisions=[],next_steps=[])
        assert frontmatter(text)['payload_sha256']==hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with pytest.raises(ValueError,match='event_id already saved with different content'):
            s.capture(**dict(arguments,outcome='A conflicting synthetic milestone.'))
        assert (vault/page).read_text(encoding='utf-8')==text
    elif route=='update':
        receipt=s.update_current('02-Areas/AI-Memory/Link Contract.md','Synthetic derived page',
                                 'This synthetic statement remains source-backed.',[relative])
        page=receipt['path'];text=(vault/page).read_text(encoding='utf-8')
        link=text.split('## Sources\n',1)[1].splitlines()[0].removeprefix('- ')
        metadata=frontmatter(text)
        assert metadata['sources']==[relative] and metadata['source_hashes']=={relative:source_sha}
    else:
        packet=w.prepare(4)
        assert [row['path'] for row in packet['sources']]==[relative]
        assert packet['sources'][0]['sha256']==source_sha
        result={'pages':[{'category':'Projects','slug':'r143-links','title':'Synthetic linked evidence',
                         'related':[],'claims':[{'text':'The source records a demonstrated synthetic prototype.',
                         'source':relative,'quote':QUOTE,'status':'historical'}]}]}
        receipts=w.apply(packet,result)
        assert w.apply(packet,result)==receipts
        assert not w.lint()['issues']
        if route=='wiki-evidence':
            page=w.BASE+'Projects/r143-links.md';text=(vault/page).read_text(encoding='utf-8')
            evidence=next(line for line in text.splitlines() if '  - Evidence: ' in line)
            link=evidence.split('Evidence: ',1)[1].split(' \u2014 ',1)[0]
            claim=frontmatter(text)['wiki_claims'][0]
            assert claim['source']==relative and claim['quote']==QUOTE
            assert claim['source_revision']['sha256']==source_sha
        else:
            page=w.BASE+'Sources/'+hashlib.sha256(relative.encode()).hexdigest()[:16]+'.md'
            text=(vault/page).read_text(encoding='utf-8')
            link=next(line for line in text.splitlines() if line.startswith('Original: ')).removeprefix('Original: ')
            assert frontmatter(text)['sources']==[relative]
        assert frontmatter(text)['source_hashes']=={relative:source_sha}
    assert source.read_bytes()==original and hashlib.sha256(source.read_bytes()).hexdigest()==source_sha
    assert {p.relative_to(vault).as_posix() for p in (vault/'03-Resources').rglob('*') if p.is_file()}=={relative}
    return page,link


@pytest.mark.parametrize('route',ROUTES,ids=ROUTES)
@pytest.mark.parametrize('relative,expected',SPECIAL_CASES,ids=SPECIAL_IDS)
def test_public_source_references_display_exact_exceptional_paths(brain,route,relative,expected):
    _,reference=_publish(brain,route,relative)
    assert reference==expected


@pytest.mark.parametrize('route',ROUTES,ids=ROUTES)
def test_ordinary_public_link_bytes_remain_unchanged(brain,route):
    relative='03-Resources/Ordinary Unicode \u03b1 (v1).md'
    _,link=_publish(brain,route,relative)
    assert link=='[['+relative[:-3]+']]'


@pytest.mark.parametrize('relative,origin',[
    ('03-Resources/line\nbreak.md','02-Areas/AI-Memory/Derived.md'),
    ('03-Resources/line\rbreak.md','02-Areas/AI-Memory/Derived.md'),
    ('03-Resources/line\u2028break.md','02-Areas/AI-Memory/Derived.md'),
    ('03-Resources/invalid\ud800.md','02-Areas/AI-Memory/Derived.md'),
    ('03-Resources/Source.md','02-Areas/AI-Memory/line\nbreak.md'),
],ids=['source-lf','source-cr','source-unicode-separator','source-invalid-utf8','origin-lf'])
def test_reference_formatter_rejects_nonrepresentable_text_without_writes(brain,relative,origin):
    vault,_=brain
    with pytest.raises((ValueError,UnicodeError)):
        s.note_link(relative,origin)
    assert list(vault.rglob('*'))==[]


def test_literal_source_reference_does_not_weaken_current_page_CAS(brain):
    vault,state=brain
    relative='03-Resources/Spec#Plan.md'
    page,_=_publish(brain,'update',relative)
    before=(vault/page).read_bytes();source=(vault/relative).read_bytes()
    with pytest.raises(ValueError,match='Concurrent change'):
        s.update_current(page,'Changed','This must not replace an owner edit.',[relative],'0'*64)
    assert (vault/page).read_bytes()==before and (vault/relative).read_bytes()==source
    expected=hashlib.sha256(before).hexdigest()
    receipt=s.update_current(page,'Changed','A reviewed synthetic revision.',[relative],expected)
    assert receipt['status']=='saved' and receipt['sha256']==hashlib.sha256((vault/page).read_bytes()).hexdigest()
    assert (state/'write-backups'/expected).read_bytes()==before
    metadata=frontmatter((vault/page).read_text(encoding='utf-8'))
    assert metadata['sources']==[relative]
    assert metadata['source_hashes']=={relative:hashlib.sha256(source).hexdigest()}
    assert (vault/relative).read_bytes()==source


@pytest.mark.parametrize('relative,expected',[
    ('`Spec#Plan.md','Source path: `` `Spec#Plan.md ``'),
    ('```Spec#Plan.md','Source path: ```` ```Spec#Plan.md ````'),
    ('03-Resources\\Folder\\Source.md','Source path: `03-Resources/Folder/Source.md`'),
],ids=['leading-backtick-padding','leading-backtick-run-padding','canonical-backslash-separators'])
def test_code_span_boundaries_preserve_canonical_path_text(brain,relative,expected):
    vault,_=brain
    assert s.note_link(relative,'02-Areas/AI-Memory/Derived.md')==expected
    assert list(vault.rglob('*'))==[]
