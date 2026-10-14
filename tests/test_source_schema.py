import copy
import pytest
from jsonschema import validate, ValidationError
from test_wiki import fixture
import brain_wiki as w


def test_model_schema_only_allows_the_current_source_paths(brain):
    _,packet,result=fixture(brain)
    schema=w.output_schema(packet)
    validate(result,schema)
    wrong=copy.deepcopy(result)
    wrong['pages'][0]['claims'][0]['source']='01-Projects/OldContextOnly.md'
    with pytest.raises(ValidationError):
        validate(wrong,schema)
    assert 'enum' not in w.SCHEMA['properties']['pages']['items']['properties']['claims']['items']['properties']['source']


def test_old_context_evidence_cannot_be_a_new_claim_source(brain):
    source,packet,result=fixture(brain)
    w.apply(packet,result)
    other=source.with_name('Continuation.md')
    other.write_text('# Example Project continuation\nA separately dated requirement remains pending.',encoding='utf-8')
    packet=w.prepare()
    assert packet['current_pages']
    allowed=w.output_schema(packet)['properties']['pages']['items']['properties']['claims']['items']['properties']['source']['enum']
    assert allowed==['01-Projects/Continuation.md']
