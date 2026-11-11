import copy
import json
import pytest
from typer.testing import CliRunner
from dalel.formats.arb import ArbDocument
from dalel.delivery.comparison import identity,run_metadata,compare,load_baseline
from dalel.cli import app

def finding(**changes):
    return dict(key='sampleLabel',issue_type='machine_translation_suspect',severity='high',
        source='День',target='Түн',suggestion='Күн',evidence='Исходное «День» передано противоположным временем суток.',
        confidence=.85,reviewed_by_human=False)|changes

def metadata(records):
    return run_metadata(records,ArbDocument(b'{"@@locale":"ru"}'),ArbDocument(b'{"@@locale":"kk"}'))

def delta(before,after):
    return compare(before,after,metadata(before),metadata(after),'high')

def test_all_baseline_states_and_severity_are_distinguished():
    row=finding()
    assert delta([row],[row])['summary']['existing']==1
    assert not delta([row],[row])['blocking']
    assert delta([],[row])['blocking']
    assert delta([row],[])['summary']['resolved']==1
    assert delta([row|{'severity':'medium'}],[row])['summary']['escalated']==1
    assert not delta([row],[row|{'severity':'medium'}])['blocking']
    changed=row|{'source':'Светлый день'}
    result=delta([row],[changed])
    assert result['summary']['changed_high_requires_review']==1 and result['blocking']
    assert result['rule_change_impact']=='not_assessed' and not result['report_filtered']

def test_fingerprints_are_full_stable_locators_not_evidence_or_value_hashes():
    row=finding();first=identity(row,'kk')
    assert len(first['fingerprint'])==64
    assert identity(row|{'severity':'medium','evidence':'Другое объяснение «День».','target':'Кеш'},'kk')==first
    placeholder=finding(issue_type='placeholder_mismatch',source='Hello {person}',target='Hallo',suggestion=None)
    a=identity(placeholder,'de')
    assert identity(placeholder|{'target':'Hallo dort'},'de')==a
    assert identity(placeholder|{'source':'Hello {other_person}'},'de')!=a
    assert identity(placeholder|{'target':'Hallo'},'fr')!=a

def test_multiplicity_and_exact_pairing_prevent_new_defects_from_disappearing():
    row=finding();changed=row|{'target':'Түн мезгілі'}
    result=delta([row,changed],[changed,row,row])
    assert result['summary']=={'existing':2,'new':1,'resolved':0,'escalated':0,'changed_high_requires_review':0}
    assert result['blocking']
    incomplete=metadata([row]);incomplete['identities']=[]
    with pytest.raises(ValueError,match='coverage'):
        compare([row],[row],incomplete,metadata([row]),'high')

def test_saved_baseline_requires_compatible_metadata_and_untampered_report(tmp_path):
    rows=[finding()];meta=metadata(rows);path=tmp_path/'base.json'
    path.write_text(json.dumps(rows),encoding='utf8')
    with pytest.raises(OSError):
        load_baseline(path,meta)
    path.with_suffix('.run.json').write_text(json.dumps(meta),encoding='utf8')
    assert load_baseline(path,meta)[0]==rows
    incompatible=copy.deepcopy(meta);incompatible['compatibility']['escaping']=True
    with pytest.raises(ValueError,match='incompatible'):
        load_baseline(path,incompatible)
    path.write_text(json.dumps([rows[0]|{'severity':'low'}]),encoding='utf8')
    with pytest.raises(ValueError,match='hash'):
        load_baseline(path,meta)

def test_affix_locator_roundtrips_through_json_metadata(tmp_path):
    rows=[finding(issue_type='suffix_harmony',source='{person}',target='{person}-ге',suggestion=None,
                  evidence='После «{person}» зафиксирован аффикс, зависящий от значения.')]
    meta=metadata(rows);path=tmp_path/'base.json'
    path.write_bytes(json.dumps(rows).encode())
    path.with_suffix('.run.json').write_bytes(json.dumps(meta).encode())
    assert load_baseline(path,meta)[1]==meta

def test_cli_keeps_full_report_and_fails_only_new_escalated_or_changed_high(tmp_path):
    source=tmp_path/'en.arb';target=tmp_path/'de.arb';base=tmp_path/'base/base.json';out=tmp_path/'head/head.json'
    source.write_bytes(b'{"@@locale":"en","greeting":"Hello {person}"}')
    target.write_bytes(b'{"@@locale":"de","greeting":"Hallo"}')
    runner=CliRunner();args=['check','--source',str(source),'--target',str(target)]
    assert runner.invoke(app,args+['--out',str(base)]).exit_code==0
    assert runner.invoke(app,args+['--out',str(out),'--baseline',str(base),'--fail-on','high']).exit_code==0
    assert len(json.loads(out.read_text(encoding='utf8')))==1
    assert json.loads(out.with_suffix('.delta.json').read_text())['summary']['existing']==1
    target.write_bytes(b'{"@@locale":"de","greeting":"Hallo dort"}')
    assert runner.invoke(app,args+['--out',str(out),'--baseline',str(base),'--fail-on','high']).exit_code==1
    assert json.loads(out.with_suffix('.delta.json').read_text())['summary']['changed_high_requires_review']==1
    target.write_bytes(b'{"@@locale":"de","greeting":"Hallo {person}"}')
    assert runner.invoke(app,args+['--out',str(out),'--baseline',str(base),'--fail-on','high']).exit_code==0
    assert json.loads(out.with_suffix('.delta.json').read_text())['summary']['resolved']==1
    assert runner.invoke(app,args+['--out',str(out),'--baseline',str(base),'--use-escaping']).exit_code==2
    before=base.read_bytes()
    assert runner.invoke(app,args+['--out',str(base),'--baseline',str(base)]).exit_code==2
    assert base.read_bytes()==before
