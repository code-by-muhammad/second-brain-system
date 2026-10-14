import pytest
from test_wiki import fixture
import brain_store as s
import brain_wiki as w


def catalog_only_update(brain):
    source,packet,result=fixture(brain)
    first=w.apply(packet,result)[0]
    other=s.VAULT/'01-Projects/Separate.md'
    quote='A later review recorded a separate verification requirement.'
    other.write_text('---\ndate: 2026-10-02\n---\n# Separate subject\n'+quote,encoding='utf-8')
    packet=w.prepare()
    assert not packet['current_pages']
    assert packet['catalog'][0]['sha256']==first['sha256']
    result['pages'][0]['claims']=[{'text':'A later source recorded a verification requirement.',
                                 'source':'01-Projects/Separate.md','quote':quote,'status':'historical'}]
    return packet,result,first


def test_catalog_only_update_preserves_prior_evidence(brain):
    packet,result,first=catalog_only_update(brain)
    w.apply(packet,result)
    page=w.meta(s.safe_path(first['path']).read_text(encoding='utf-8'))
    assert len(page['wiki_claims'])==2
    assert {c['source'] for c in page['wiki_claims']}=={'01-Projects/Example.md','01-Projects/Separate.md'}
    assert not w.lint()['issues']


def test_catalog_revision_prevents_concurrent_overwrite(brain):
    packet,result,first=catalog_only_update(brain)
    target=s.safe_path(first['path']); target.write_text(target.read_text(encoding='utf-8')+'Owner edit.\n',encoding='utf-8')
    before=target.read_bytes()
    with pytest.raises(ValueError,match='Wiki page changed'):
        w.apply(packet,result)
    assert target.read_bytes()==before


def test_claim_budget_includes_history_outside_the_bounded_context(brain):
    source,packet,result=fixture(brain)
    base=result['pages'][0]['claims'][0]
    result['pages'][0]['claims']=[{**base,'text':f'Synthetic supported requirement {i}.'} for i in range(30)]
    w.apply(packet,result)
    source.write_text(source.read_text(encoding='utf-8')+'\nA later continuation.',encoding='utf-8')
    packet=w.prepare()
    assert packet['catalog'][0]['claim_count']==30
    assert packet['current_pages'][0]['claim_count']==30
    assert len(packet['current_pages'][0]['meta']['wiki_claims'])==12


@pytest.mark.parametrize('returncode,diagnostic,expected',[
    (0,'ERROR: 429 Too Many',False),
    (1,'',False),
    (1,'ERROR: authentication failed',True),
    (1,'ERROR: quota exceeded',True),
])
def test_only_failed_model_diagnostics_can_trigger_global_cooldown(returncode,diagnostic,expected):
    model_input='Compile sources\nINPUT_JSON:\n{"source":"rate limit 429 Too Many is historical source text"}'
    runner='user\n'+model_input+'\n'+diagnostic
    assert w._model_auth_quota_failure(returncode,runner,model_input)==expected
