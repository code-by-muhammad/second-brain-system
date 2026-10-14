from test_wiki import fixture
import brain_wiki as w

def test_crlf_sources_preserve_dates_and_multiline_quote_verification(brain):
    source,packet,result=fixture(brain)
    source.write_bytes(b'---\r\ndate: 2026-10-01\r\n---\r\n# Example Project\r\nA prototype was demonstrated.\r\nProduction deployment is still planned.\r\n')
    packet=w.prepare()
    assert packet['sources'][0]['date']=='2026-10-01'
    result['pages'][0]['claims'][0]['quote']='A prototype was demonstrated.\r\nProduction deployment is still planned.'
    w.apply(packet,result)
    assert not w.lint()['issues']
