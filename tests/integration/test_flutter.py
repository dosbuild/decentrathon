import json

import pytest

from dalel.formats.arb import ArbDocument
from dalel.delivery.flutter import without_members,key_mapping,normalize_document,verify_bundle


@pytest.mark.parametrize('keys',[set(),{'Bad'},{'last'},{'first','last'},{'first','Bad','last'}])
def test_template_exclusions_preserve_remaining_literals(keys):
    raw=b' {\n "first": "A\\u0020", "Bad" : "B",\n "last":"\\ue012"\n }\n'
    doc=ArbDocument(raw)
    output=without_members(doc,keys)
    result=ArbDocument(output)
    assert set(result.strings)==set(doc.strings)-keys
    if not keys:
        assert output==raw
    for key,member in result.effective.items():
        old=doc.effective[key]
        assert result.text[member.start:member.end]==doc.text[old.start:old.end]


def test_synchronous_normalization_and_metadata():
    doc=ArbDocument(b'{"@@locale":"en","Upper":"A","valid_name":"B","item":"C","item@description":"Context","@Upper":{"description":"Greeting"}}')
    mapping,actions=key_mapping([doc])
    result=ArbDocument(normalize_document(doc,mapping,actions))
    assert mapping=={'Upper':'upper','valid_name':'valid_name','item':'item'}
    assert result.effective['@item'].value=={'description':'Context'}
    assert result.effective['@upper'].value=={'description':'Greeting'}
    assert [a['kind'] for a in actions]==['rename','move']


def test_normalization_conflicts_are_errors():
    with pytest.raises(ValueError,match='collision'):
        key_mapping([ArbDocument(b'{"Upper":"A","upper":"B"}')])
    doc=ArbDocument(b'{"item":"C","item@description":"New","@item":{"description":"Old"}}')
    mapping,actions=key_mapping([doc])
    with pytest.raises(ValueError,match='Conflicting'):
        normalize_document(doc,mapping,actions)


def test_template_description_is_canonical_without_overwriting_conflicts():
    source=ArbDocument(b'{"item":"Source","item@description":"Canonical"}')
    target=ArbDocument(b'{"item":"Target","item@description":"Local description"}')
    mapping,actions=key_mapping([source,target])
    result=ArbDocument(normalize_document(target,mapping,actions,source))
    assert result.effective['@item'].value['description']=='Canonical'
    conflict=ArbDocument(b'{"item":"Target","item@description":"Local description","@item":{"description":"Keep"}}')
    with pytest.raises(ValueError,match='Conflicting'):
        normalize_document(conflict,mapping,actions,source)


def test_missing_sdk_is_explicit_not_run(tmp_path,monkeypatch):
    monkeypatch.setenv('LOCQA_FLUTTER',str(tmp_path/'absent-sdk'))
    out=tmp_path/'result.json'
    result=verify_bundle(tmp_path,out)
    assert result['status']=='not_run' and not result['checks']
    assert json.loads(out.read_text(encoding='utf8'))==result


def test_validation_does_not_overwrite_input(tmp_path):
    path=tmp_path/'intl_ru.arb'
    path.write_bytes(b'{}')
    with pytest.raises(ValueError,match='overwrite'):
        verify_bundle(tmp_path,path)
    assert path.read_bytes()==b'{}'
