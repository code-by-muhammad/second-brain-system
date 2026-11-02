"""Final UTF-8 output must fit the reader before any note publication."""
import copy
from pathlib import Path
import pytest
import brain_store as s
import brain_wiki as w
import brain_settings as cfg
from test_wiki import fixture

@pytest.mark.parametrize('size',[2_000_000,2_000_001])
def test_planned_output_exact_byte_boundary(brain,size):
    text=chr(0x1f642)*500000+('x' if size>2_000_000 else '')
    writes=[];target=s.safe_path(w.BASE+'Projects/boundary.md')
    if size>2_000_000:
        with pytest.raises(w.WikiOutputError,match='byte limit'):w._plan_write(writes,target,text,'wiki_compile',None)
        assert not writes and not target.exists()
    else:
        w._plan_write(writes,target,text,'wiki_compile',None)
        assert len(writes)==1 and len(writes[0]['text'].encode('utf-8'))==size

def test_saved_plan_cannot_bypass_output_byte_guard(brain):
    _,packet,result=fixture(brain)
    plan=w._publication_plan(packet,result,w._dispositions(packet,result))
    row=next(row for row in plan['writes'] if row['kind']=='wiki_compile')
    row['text']=chr(0x1f642)*500000+'x';row['after']=s.digest(row['text'].encode())
    with pytest.raises(w.WikiOutputError,match='byte limit'):w._verify_publication_plan(plan,packet,result)
    assert not (s.VAULT/w.BASE/'Projects').exists()

def test_legal_multibyte_sixty_to_ninety_merge_refuses_whole_plan(brain,monkeypatch):
    vault,_=brain;folder=vault/'01-Projects';folder.mkdir()
    quote=chr(0x1f642)*1200
    def batch(number):
        source=folder/(str(number)+'.md');source.write_text('---\ndate: 2026-10-01\n---\n# Historical evidence\n'+quote+'\n',encoding='utf-8')
        packet=w.prepare(4)
        result={'pages':[{'category':'Projects','slug':'emoji','title':'Historical emoji topic','related':[],
                           'claims':[{'text':str(i)+' '+chr(0x1f642)*1595,'source':source.relative_to(vault).as_posix(),
                                      'quote':quote,'status':'historical'} for i in range(number*30,(number+1)*30)]}]}
        return source,packet,result
    for number in range(2):
        _,packet,result=batch(number);w.apply(packet,result)
    topic=vault/w.BASE/'Projects/emoji.md'
    assert len(w.meta(topic.read_text(encoding='utf-8'))['wiki_claims'])==60 and topic.stat().st_size<2_000_000
    source,packet,result=batch(2)
    before={p.relative_to(vault).as_posix():p.read_bytes() for p in vault.rglob('*.md')}
    def refuse(*args,**kwargs):pytest.fail('Oversize plan reached note/archive publication.')
    monkeypatch.setattr(s,'_write',refuse);monkeypatch.setattr(w,'_stream_publish',refuse)
    with pytest.raises(w.WikiOutputError,match='byte limit'):w.apply(packet,result)
    assert {p.relative_to(vault).as_posix():p.read_bytes() for p in vault.rglob('*.md')}==before
    assert not (cfg.STATE/'wiki/batches'/packet['batch_id']/'publication.json').exists()
