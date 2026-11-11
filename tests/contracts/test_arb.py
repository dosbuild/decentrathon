import json

import pytest

from dalel.formats.arb import ArbDocument, string_literal


RAW = b' \xef\xbb\xbf'  # BOM is supported only at the beginning, as UTF-8 specifies.


def test_no_edits_and_effective_duplicate():
    raw = b'\xef\xbb\xbf {\r\n  "x": "old",\r\n   "@@comment": "a",\r\n  "x": "new", "@x":{"description":"keep"}\r\n}\r\n'
    doc = ArbDocument(raw)
    assert doc.strings == {'x':'new'}
    assert doc.render() == raw
    changed = doc.render({'x':'fixed'})
    assert changed == raw.replace(b'"new"',b'"fixed"')
    assert doc.snapshot()['duplicates'] == 1
    assert doc.snapshot()['conflicts'] == 1


def test_nested_metadata_duplicates_are_diagnosed_without_rewriting_bytes():
    from dalel.engine.rules import check
    raw=b'{"@@locale":"de","label":"Wert {n}","@label":{"placeholders":{"n":{"type":"int","type":"num"}},"notes":[{"hint":"a","hint":"b"}]}}'
    target=ArbDocument(raw)
    source=ArbDocument(b'{"@@locale":"en","label":"Value {n}","@label":{"placeholders":{"n":{"type":"num"}}}}')
    assert target.render()==raw
    assert target.render({'label':'Zahl {n}'})==raw.replace(b'Wert {n}',b'Zahl {n}')
    assert target.effective['@label'].value['placeholders']['n']['type']=='num'
    _,diagnostics=check(source,target)
    nested=[d for d in diagnostics if d['type']=='nested_duplicate']
    assert {tuple(d['path']) for d in nested}=={('@label','placeholders','n','type'),('@label','notes',0,'hint')}
    assert all(d['file']=='target' and d['occurrences']==2 and d['conflict'] for d in nested)
    assert source.nested_duplicates==[]


@pytest.mark.parametrize(('old','new','expected'),[
    ('"A\\uea34\\u0020B"','C\uea34 B','"C\\uea34\\u0020B"'),
    ('"\\ud83d\\ude80 ship"','🚀 boat','"\\ud83d\\ude80 boat"'),
    ('"\\u04d8"','Әл','"\\u04d8\\u043b"'),
    ('"a\\/b"','c/b','"c\\/b"'),
])
def test_escape_style(old,new,expected):
    assert string_literal(new,old) == expected
    assert json.loads(expected) == new


def test_insertions_are_minimal_and_ordered():
    raw = b'{\n  "a": "A",\n  "d": "D"\n}\n'
    doc = ArbDocument(raw)
    result = doc.insert_in_source_order({'b':'B','c':'C','e':'E'},['a','b','c','d','e'])
    assert result == b'{\n  "a": "A",\n  "b": "B",\n  "c": "C",\n  "d": "D",\n  "e": "E"\n}\n'
    assert list(ArbDocument(result).strings) == list('abcde')
    assert ArbDocument(ArbDocument(b'{}').insert('a','A')).strings == {'a':'A'}


@pytest.mark.parametrize('raw',[b'[]',b'{"x":1,}',b'{"x":NaN}',b'{"x":"a"}junk',RAW+b'{}'])
def test_malformed_input_is_rejected(raw):
    with pytest.raises(ValueError):
        ArbDocument(raw)
