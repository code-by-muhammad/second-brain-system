from test_wiki import fixture
import brain_wiki as w

def test_category_relative_links_only_normalize_known_targets(brain):
    source,packet,result=fixture(brain)
    other={**result['pages'][0],'slug':'second','related':['Projects/example.md']}
    result['pages'].append(other)
    assert len(w.apply(packet,result))==2
    assert not w.lint()['issues']
