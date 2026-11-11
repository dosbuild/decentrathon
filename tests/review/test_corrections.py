import json
import pytest
from dalel.formats.arb import ArbDocument
from dalel.engine.corrections import apply_ai_changes

def test_ai_export_is_minimal_ordered_snapshot_bound_and_never_human():
    source=ArbDocument(b'{"@@locale":"en","first":"Open {item}","newEntry":"Help","last":"Next"}')
    raw=b' {\n  "@@locale":"de",\n  "first":"shadow",\n  "first":"Zu",\n  "@first":{"description":"keep","reviewed_by_human":true,"x-locqa":{"unknown":7}},\n  "last":"Weiter"\n}\n'
    target=ArbDocument(raw)
    rows=[{'key':'first','source':'Open {item}','target':'Zu','suggestion':'Auf {item}','evidence':'Неверно «Zu».'},
          {'key':'newEntry','source':'Help','target':None,'suggestion':'Hilfe','evidence':'Нет «Help».'}]
    fixed,changes=apply_ai_changes(source,target,rows,provenance={'synthetic':True})
    doc=ArbDocument(fixed)
    assert list(doc.strings)==['first','newEntry','last']
    assert [m.value for m in doc.members if m.key=='first']==['shadow','Auf {item}']
    assert b'"last":"Weiter"' in fixed and fixed.startswith(b' {\n')
    assert doc.effective['@first'].value['description']=='keep'
    assert doc.effective['@first'].value['x-locqa']['unknown']==7
    assert 'source_sha256' not in doc.effective['@first'].value['x-locqa']
    assert changes[0]['provenance']['source_sha256']
    assert all(doc.effective['@'+r['key']].value['reviewed_by_human'] is False for r in changes)
    assert target.raw==raw and not any(r['reviewed_by_human'] for r in changes)
    assert apply_ai_changes(source,target,[],provenance={})==(raw,[])
    with pytest.raises(ValueError,match='snapshot'):
        apply_ai_changes(source,target,[dict(rows[0],target='other')],provenance={})
    with pytest.raises(ValueError,match='Unsafe'):
        apply_ai_changes(source,target,[dict(rows[0],suggestion='Auf')],provenance={})
    with pytest.raises(ValueError,match='Conflicting'):
        apply_ai_changes(source,target,[rows[0],dict(rows[0],suggestion='Offen {item}')],provenance={})


@pytest.mark.parametrize('raw',[b' {"a":"x","b":"y","c":"z"}',b'{\r\n   "a":"x",\r\n   "b":"y",\r\n   "c":"z"\r\n}\r\n'])
def test_bulk_metadata_insertion_is_byte_identical_to_sequential_insertion(raw):
    original=ArbDocument(raw);sequential=original
    additions={'@a':('a',{'reviewed_by_human':False}),'@c':('c',{'note':'new'}),'@b':('b',{'other':3})}
    for name,(anchor,value) in additions.items():sequential=ArbDocument(sequential.insert(name,value,after=anchor))
    assert original.insert_after_many(additions)==sequential.raw
