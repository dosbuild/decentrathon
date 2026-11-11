import json
import sqlite3
import pytest
from typer.testing import CliRunner
from dalel.formats.arb import ArbDocument
from dalel.cli import app
from dalel.review.store import ReviewStore
from test_production import document

def fixture(tmp_path):
    source=document('en',entry='Open {item}',next='Next')
    target=ArbDocument((' {\n  "@@locale": "de",\n  "@@comment":"first",\n  "entry":"Alt",\n'
        '  "entry":"Zumachen",\n  "@entry":{"description":"Keep this","x-locqa":{"unknown":7}},\n'
        '  "@@comment":"second",\n  "next":"Weiter"\n}\n').encode())
    finding={'key':'entry','source':'Open {item}','target':'Zumachen','issue_type':'machine_translation_suspect',
        'severity':'high','evidence':'В «Zumachen» закрытие вместо открытия из исходника.',
        'suggestion':'Öffne {item}','confidence':.9,'reviewed_by_human':False}
    store=ReviewStore(tmp_path/'review.sqlite3')
    snapshot=store.register(source,target,[finding]);fid=store.findings(snapshot)[0]['id']
    return store,snapshot,source,target,finding,fid

def action(store,snapshot,kind,**kwargs):
    return store.decide(snapshot,'entry',kind,revision=store.state(snapshot,'entry',include_simulated=True)['revision'],**kwargs)

def test_new_draft_does_not_replace_approval_and_revocation_never_falls_back(tmp_path):
    store,snapshot,source,target,finding,fid=fixture(tmp_path)
    action(store,snapshot,'approve',text='Öffne {item}',finding_id=fid)
    action(store,snapshot,'draft',text='Bitte öffne {item}')
    simulation=store.export(snapshot,simulation=True)
    assert simulation['changes'][0]['after']=='Öffne {item}'
    assert simulation['report'][0]['suggestion']=='Öffne {item}'
    assert not simulation['changes'][0]['reviewed_by_human']
    assert store.export(snapshot)['fixed']==target.raw
    assert store.export(snapshot)['summary']['human_accepted_strings']==0
    action(store,snapshot,'approve',text='Bitte öffne {item}',finding_id=fid)
    action(store,snapshot,'revoke')
    assert store.export(snapshot,simulation=True)['fixed']==target.raw
    assert store.state(snapshot,'entry',include_simulated=True)['approval'] is None

def test_late_original_finding_stays_eligible_and_needs_explicit_link(tmp_path):
    store,snapshot,source,target,finding,fid=fixture(tmp_path)
    action(store,snapshot,'approve',text='Öffne {item}',finding_id=fid)
    late=dict(finding,issue_type='placeholder_mismatch',evidence='Утерян аргумент «{item}» из исходника.',suggestion='Öffnen: {item}')
    assert store.register(source,target,[late])==snapshot
    late_id=next(f['id'] for f in store.findings(snapshot) if f['issue_type']=='placeholder_mismatch')
    before=store.export(snapshot,simulation=True)
    assert len(before['report'])==2 and before['report'][1]['suggestion']=='Öffnen: {item}'
    assert not before['report'][1]['reviewed_by_human']
    action(store,snapshot,'link',finding_id=late_id)
    assert store.export(snapshot,simulation=True)['report'][1]['suggestion']=='Öffne {item}'
    changed=document('en',entry='Unlock {item}',next='Next')
    new=store.register(changed,target,[dict(finding,source='Unlock {item}')])
    assert new!=snapshot and store.export(new,simulation=True)['changes']==[]

def test_minimal_writer_preserves_duplicate_occurrences_metadata_and_unrelated_bytes(tmp_path):
    store,snapshot,source,target,finding,fid=fixture(tmp_path)
    action(store,snapshot,'approve',text='Öffne {item}',finding_id=fid)
    result=store.export(snapshot,simulation=True);fixed=ArbDocument(result['fixed'])
    assert [m.value for m in fixed.members if m.key=='entry']==['Alt','Öffne {item}']
    assert fixed.effective['@entry'].value['description']=='Keep this'
    assert fixed.effective['@entry'].value['x-locqa']['unknown']==7
    assert fixed.effective['@entry'].value['reviewed_by_human'] is False
    assert result['fixed'].startswith(b' {\n') and b'"next":"Weiter"' in result['fixed']
    assert [m.value for m in fixed.members if m.key=='@@comment']==['first','second']
    assert target.raw==store.export(snapshot)['fixed']
    assert result['fixed']==store.export(snapshot,simulation=True)['fixed']

def test_exact_text_structural_safety_stale_cards_and_immutable_chain(tmp_path):
    store,snapshot,source,target,finding,fid=fixture(tmp_path)
    with pytest.raises(ValueError,match='Unsafe'):
        action(store,snapshot,'approve',text='Öffnen',finding_id=fid)
    event=action(store,snapshot,'draft',text='Öffne {item}')
    assert event['actor']=='synthetic-test' and event['human'] is False
    with pytest.raises(ValueError,match='Stale'):
        store.decide(snapshot,'entry','approve',revision=0,text='Öffne {item}',finding_id=fid)
    with store.connect() as db:
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            db.execute('DELETE FROM events')
    with store.connect() as db:
        db.execute('DROP TRIGGER immutable_events_UPDATE')
        payload=json.loads(db.execute('SELECT payload FROM events').fetchone()[0]);payload['text']='tampered'
        db.execute('UPDATE events SET payload=?',(json.dumps(payload),))
    with pytest.raises(ValueError,match='hash chain'):
        store.verify()

def test_rejection_is_per_finding_and_simulated_decisions_never_affect_human_export(tmp_path):
    store,snapshot,source,target,finding,fid=fixture(tmp_path)
    second=dict(finding,issue_type='placeholder_mismatch',evidence='Утерян аргумент «{item}» из исходника.')
    store.register(source,target,[second])
    with pytest.raises(ValueError,match='reason'):
        action(store,snapshot,'reject',finding_id=fid)
    action(store,snapshot,'reject',finding_id=fid,reason='Synthetic test only')
    assert len(store.export(snapshot,simulation=True)['report'])==1
    assert len(store.export(snapshot)['report'])==2
    assert store.export(snapshot)['summary']['human_confirmed_findings']==0

def test_cli_refuses_noninteractive_approval_session(tmp_path):
    store,snapshot,source,target,finding,fid=fixture(tmp_path)
    src=tmp_path/'en.arb';tgt=tmp_path/'de.arb';report=tmp_path/'report.json'
    src.write_bytes(source.raw);tgt.write_bytes(target.raw);report.write_bytes(json.dumps([finding]).encode())
    result=CliRunner().invoke(app,['review','--source',str(src),'--target',str(tgt),'--report',str(report),
        '--reviewer','synthetic-test','--project',str(tmp_path/'blocked.sqlite3')],input='A\n')
    assert result.exit_code==2 and 'interactive terminal' in result.output
    assert not (tmp_path/'blocked.sqlite3').exists()


def test_missing_string_insertion_is_ordered_and_marks_only_simulated_provenance(tmp_path):
    source=document('en',first='First',absent='Open {item}',last='Last')
    target=document('de',first='Erste',last='Letzte')
    finding={'key':'absent','source':'Open {item}','target':None,'issue_type':'untranslated','severity':'high',
        'evidence':'Нет перевода «Open {item}» из исходника.','suggestion':'Öffne {item}',
        'confidence':.99,'reviewed_by_human':False}
    store=ReviewStore(tmp_path/'missing.sqlite3');snapshot=store.register(source,target,[finding])
    store.decide(snapshot,'absent','approve',revision=0,text='Öffne {item}',finding_id=store.findings(snapshot)[0]['id'])
    simulation=store.export(snapshot,simulation=True);fixed=ArbDocument(simulation['fixed'])
    assert list(fixed.strings)==['first','absent','last']
    assert fixed.effective['@absent'].value['reviewed_by_human'] is False
    assert simulation['changes'][0]['before'] is None
    assert store.export(snapshot)['fixed']==target.raw
