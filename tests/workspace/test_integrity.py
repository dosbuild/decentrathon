"""Version, export and language-column contracts using tiny local fixtures."""
import copy
import csv
import io
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from typer.testing import CliRunner
from dalel.cli import app
from dalel.delivery.replay import package_report
from dalel.formats.arb import ArbDocument
from dalel.workspace.glossary import glossary_text, normalize_terms
from dalel.workspace.service import WorkspaceService
from test_connected import candidate, edited, decision


def test_active_findings_and_history_are_separate(tmp_path):
    service=WorkspaceService(tmp_path)
    state=service.create(candidate(target='{"@@locale":"de","welcome":"Hallo"}'))
    finding=state['entries']['welcome']['localFindings'][0]
    state=decision(service,state,'dismissed',findingId=finding['id'])
    release=service.release(state['id'],state['backend']['version'],'draft')
    assert json.loads(release['files']['report.json'])==[]
    assert len(json.loads(release['files']['review_state.json'])['findings'])==1
    state=edited(service,state,'Hallo {name}')
    release=service.release(state['id'],state['backend']['version'],'draft')
    assert json.loads(release['files']['report.json'])==[]
    assert not json.loads(release['files']['review_state.json'])['findings'][0]['current_analysis']


def test_glossary_changes_revoke_approval_and_receipts(tmp_path):
    from dalel.workspace.decisions import export
    service=WorkspaceService(tmp_path)
    state=decision(service,edited(service,service.create(candidate())))
    receipt=export(state)
    proposal=copy.deepcopy(state)
    proposal['glossary']=[dict(ru='Title',kk='Atau',en='Title',status='agreed')]
    state=service.save(state['id'],state['backend']['version'],proposal)
    assert not service.approval(state,'welcome')
    assert state['entries']['welcome']['reviewHistory']
    with pytest.raises(ValueError):service.import_review(state['id'],state['backend']['version'],receipt)


def test_only_inherited_line_breaks_are_guidance_and_pua_stays_blocking(tmp_path):
    service=WorkspaceService(tmp_path)
    state=service.create(candidate(source='{"@@locale":"en","welcome":"Hello\\n{name}"}'))
    assert not state['entries']['welcome']['backendValidation']['errors']
    assert state['entries']['welcome']['backendValidation']['warnings']
    state=edited(service,state,'Guten Tag {name}')
    assert not state['entries']['welcome']['backendValidation']['errors']
    state=edited(service,state,'A\nB\n{name}','new-layout')
    assert any('newline' in x['message'] for x in state['entries']['welcome']['backendValidation']['errors'])
    state=service.create(candidate(source='{"@@locale":"en","welcome":"Hello {name} \\ue012"}'))
    assert any('protected pua' in x['message'] for x in state['entries']['welcome']['backendValidation']['errors'])


def test_reviewed_release_needs_literal_flutter_proof(tmp_path):
    service=WorkspaceService(tmp_path)
    state=service.create(candidate())
    assert service.release(state['id'],state['backend']['version'],'validated')['blocked']
    with pytest.raises(ValueError,match='literal Flutter'):service.prepare(state['id'],state['backend']['version'],'validated')
    assert not service.release(state['id'],state['backend']['version'],'draft')['blocked']


def test_browser_csv_preserves_uzbek_variants_and_unknown_columns(tmp_path):
    node=shutil.which('node')
    if not node:pytest.skip('Node unavailable')
    root=Path(__file__).resolve().parents[2]
    script="const fs=require('fs'),vm=require('vm'),c=vm.createContext({TextEncoder});vm.runInContext(fs.readFileSync(process.argv[1],'utf8')+';globalThis.parse=parseCSV;',c);process.stdout.write(JSON.stringify(c.parse('ru,kk,en,uz,uz_variants,custom\\nTitle,Atau,Title,Sarlavha,Sarlavhalar,keep\\n')));"
    response=subprocess.run([node,'-e',script,str(root/'src/dalel/assets/workspace/src/core.js')],check=True,capture_output=True,text=True)
    terms=json.loads(response.stdout)
    assert terms[0]['uz']=='Sarlavha' and terms[0]['custom']=='keep'
    terms[0]['comment']='Original usage'; terms[0]['notes']=''
    assert normalize_terms(terms)[0]['notes']=='Original usage'
    terms[0]['variants']=['Kazakh-only']
    row=next(csv.DictReader(io.StringIO(glossary_text(normalize_terms(terms),analysis=True,target_locale='uz-Latn'))))
    assert row['uz_variants']=='Sarlavhalar' and row['custom']=='keep'


def test_browser_terms_match_tokens_and_explicit_forms():
    node=shutil.which('node')
    if not node:pytest.skip('Node unavailable')
    root=Path(__file__).resolve().parents[2]
    script=r"""
const fs=require('fs'),vm=require('vm'),c=vm.createContext({TextEncoder});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8')+';globalThis.match=matchingForm;globalThis.relevant=relevantTerms;globalThis.occurrences=termOccurrences;',c);
const checks=[
 ['Болдырмау','л',false],['Көлік','л',false],['25 л','л',true],
 ['25л','л',true],['3,5л','л',true],['100 л.с.','л',false],
 ['100 л.с.','л.',false],['Объём: 3 л.','л',true],
 ['ABGC','GC',false],['Баланс GC','GC',true],
 ['C++ guide','C++',true],['CX guide','C++',false],['item_label','л',false]
];
for(const [text,term,want] of checks){if(c.match(text,term)!==want)throw Error(JSON.stringify({text,term,want}));}
const litre={id:'volume',ru:'л',kk:'л',en:'L',_rule:{source_forms:['л','л.']}};
const explicit={id:'form',ru:'кнопка',kk:'батырма',en:'button',_rule:{source_forms:['кнопки'],target_term:'батырма',allowed_variants:['батырманы']}};
const ws={targetLocale:'kk',glossary:[litre,explicit],entries:{a:{source:'Кнопки',workingText:'Батырманы таңдаңыз'},b:{source:'Объём 2л',workingText:'2 л'}}};
if(JSON.stringify(c.relevant(ws,ws.entries.a).map(t=>t.id))!==JSON.stringify(['form']))throw Error('Unrelated volume term in context');
if(c.occurrences(ws,litre).length!==1)throw Error('Incorrect volume occurrence count');
"""
    subprocess.run([node,'-e',script,str(root/'src/dalel/assets/workspace/src/core.js')],check=True,capture_output=True,text=True)


def test_expert_preparation_starts_with_proposals_and_preserves_originals(tmp_path):
    source=ArbDocument(b'{"@@locale":"en","command":"Open {item}"}')
    target=ArbDocument(b'{"@@locale":"de","command":"Zu {item}"}')
    finding=dict(key='command',source='Open {item}',target='Zu {item}',suggestion='Auf {item}',
        issue_type='machine_translation_suspect',severity='high',confidence=.9,
        evidence='В «Zu {item}» противоположное действие.',reviewed_by_human=False)
    bundle=tmp_path/'saved'
    package_report(source,target,[finding],bundle,coverage=dict(status='complete',source_strings_evaluated=1),
        provenance=dict(producer='external'),verified_fixes=[dict(key='command',text='Auf {item}',verdict='strict_win')])
    before={p.relative_to(bundle):p.read_bytes() for p in bundle.rglob('*') if p.is_file()}
    output=tmp_path/'expert'
    result=CliRunner().invoke(app,['prepare-review','--bundle',str(bundle),'--out',str(output)])
    assert result.exit_code==0,result.output
    state=WorkspaceService(output/'workspaces').current()
    assert state['entries']['command']['workingText']=='Auf {item}'
    assert state['entries']['command']['originalTarget']=='Zu {item}'
    assert not WorkspaceService.approval(state,'command')
    assert before=={p.relative_to(bundle):p.read_bytes() for p in bundle.rglob('*') if p.is_file()}
    assert CliRunner().invoke(app,['prepare-review','--bundle',str(bundle),'--out',str(output)]).exit_code==2


def test_transliteration_does_not_inherit_nested_human_receipt():
    from dalel.languages.latin import convert_document
    source=ArbDocument(json.dumps({'@@locale':'kk','label':'Қала',
        '@label':{'reviewed_by_human':True,'custom':'keep',
                  'locqaReview':{'reviewed_by_human':True,'revision_id':'cyrillic-fixture','reviewer':'Synthetic fixture'}}},ensure_ascii=False).encode())
    raw,report=convert_document(source)
    result=json.loads(raw);meta=result['@label']
    assert result['label']!='Қала' and meta['custom']=='keep'
    assert meta['reviewed_by_human'] is False
    assert meta['locqaReview']['reviewed_by_human'] is False
    assert meta['locqaReview']['reviewer'] is None
    assert meta['sourceReview']['revision_id']=='cyrillic-fixture'
    assert report['human_review']=='not_reviewed'


def test_affix_purchase_rephrasing_keeps_runtime_token_and_edges():
    from dalel.languages.kazakh import purchase_affix_rephrase
    from dalel.engine.rules import check
    assert purchase_affix_rephrase('Купить за {amount}',' {amount}-ге сатып алу ')==' {amount} бағасына сатып алу '
    assert purchase_affix_rephrase('Подарить {name}','{name}-ға сатып алу') is None
    assert purchase_affix_rephrase('Купить за {amount}','{name}-ге сатып алу') is None
    source=ArbDocument('{"@@locale":"ru","arbitrary":"Купить за {amount}"}'.encode())
    target=ArbDocument('{"@@locale":"kk","arbitrary":"{amount}-ге сатып алу"}'.encode())
    rows,diagnostics=check(source,target)
    finding=next(row for row in rows if row.issue_type=='suffix_harmony')
    assert finding.suggestion=='{amount} бағасына сатып алу'
    assert not finding.reviewed_by_human
    assert not any(d['type']=='requires_code_change' for d in diagnostics)
