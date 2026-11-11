"""Connected service contracts. All actors and inputs here are synthetic tests."""
import copy
import io
import json
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest
from dalel.workspace.service import WorkspaceService, Conflict, digest, glossary_text
from dalel.workspace.server import WorkspaceHTTPServer


def candidate(source=None, target=None):
    source = source or '{\n "@@locale":"en", "welcome":"Hello {name}", "@welcome":{"description":"Welcome", "placeholders":{"name":{"type":"String"}}}\n}\n'
    target = target or '{\n "@@locale":"de", "welcome":"Hallo {name}", "@welcome":{"description":"Greeting", "custom":{"keep":true}}\n}\n'
    return {'name':'Contract test', 'sourceLocale':'en', 'targetLocale':'de',
            'snapshots':{'source':{'name':'source.arb','raw':source}, 'target':{'name':'target.arb','raw':target},'references':[]}, 'glossary':[]}


def edited(s, w, text='Guten Tag {name}', rid='draft-1'):
    next_state = copy.deepcopy(w)
    next_state['entries']['welcome'].update(workingText=text, currentRevisionId=rid)
    return s.save(w['id'],w['backend']['version'],next_state)


def decision(s,w,action='approve',**kw):
    e=w['entries']['welcome']
    return s.decide(w['id'],w['backend']['version'],dict(key='welcome', revisionId=e['currentRevisionId'],text=e['workingText'],action=action,reviewer='Synthetic contract fixture',attestHuman=True,**kw))


def test_real_engine_lossless_raw_and_offline_coverage(tmp_path):
    s=WorkspaceService(tmp_path)
    w=s.create(candidate())
    assert w['snapshots']['target']['raw']==candidate()['snapshots']['target']['raw']
    assert w['backend']['analysis']['api_calls']==0
    assert w['backend']['analysis']['language_complete'] is False
    r=s.release(w['id'],1,'validated')
    assert r['files'][r['filename']]==candidate()['snapshots']['target']['raw']
    assert r['blocked'] and r['coverage']['matches_selected_output'] and not r['errors']
    assert set(r['files'])=={'intl_de_fixed.arb','report.json','report.html','glossary.csv','methodology.md','changes.json','coverage.json','review_state.json'}
    assert WorkspaceService(tmp_path).current()==w


def test_exact_version_approval_revert_save_forgery_and_conflict(tmp_path):
    s=WorkspaceService(tmp_path); w=s.create(candidate()); old=copy.deepcopy(w)
    w=edited(s,w); w=decision(s,w)
    assert s.approval(w,'welcome')
    r=s.release(w['id'],w['backend']['version'],'validated')
    fixed=json.loads(r['files'][r['filename']]);assert fixed['welcome']=='Guten Tag {name}'
    assert fixed['@welcome']['custom']=={'keep':True} and fixed['@welcome']['reviewed_by_human'] is True
    assert not r['coverage']['matches_selected_output']
    before=copy.deepcopy(w)
    w=edited(s,w,'Anders {name}','draft-2'); assert not s.approval(w,'welcome')
    w=edited(s,w,'Guten Tag {name}',before['entries']['welcome']['currentRevisionId'])
    assert not s.approval(w,'welcome') and w['entries']['welcome']['reviewHistory']
    forged=copy.deepcopy(w);forged['entries']['welcome']['decisions']=before['entries']['welcome']['decisions'];forged['processing']['externalCost']=999
    w=s.save(w['id'],w['backend']['version'],forged)
    assert not s.approval(w,'welcome') and w['processing']['externalCost']==0
    with pytest.raises(Conflict): s.save(w['id'],old['backend']['version'],old)
    assert s.get(w['id'])==w


def test_backup_claims_are_history_and_require_fresh_review(tmp_path):
    s=WorkspaceService(tmp_path);w=decision(s,edited(s,s.create(candidate())))
    recovered=s.create(w)
    assert recovered['entries']['welcome']['workingText']=='Guten Tag {name}'
    assert not s.approval(recovered,'welcome')
    assert recovered['recoveredHistory']['trusted_human_review'] is False
    assert recovered['recoveredHistory']['workspace']==w


def test_simulation_and_finding_judgment_never_approve_translation(tmp_path):
    s=WorkspaceService(tmp_path,simulation=True);w=s.create(candidate())
    w=decision(s,edited(s,w));assert not s.approval(w,'welcome')
    assert w['entries']['welcome']['reviewHistory'][-1]['action']=='simulated_approval'
    w=s.manual(w['id'],w['backend']['version'],{'key':'welcome','revisionId':w['entries']['welcome']['currentRevisionId'],'issue_type':'length_overflow','severity':'low','evidence':'Synthetic note','suggestion':None})
    f=w['entries']['welcome']['manualFindings'][0]
    w=decision(s,w,'dismissed',findingId=f['id'])
    assert not w['entries']['welcome']['manualFindings'][0]['judgments'] and not s.approval(w,'welcome')
    r=s.release(w['id'],w['backend']['version'],'draft')
    assert all(not c['reviewed_by_human'] for c in r['changes'])
    assert all(not f['reviewed_by_human'] for f in json.loads(r['files']['review_state.json'])['findings'])


def test_human_finding_and_sentence_are_independent(tmp_path):
    s=WorkspaceService(tmp_path);w=s.create(candidate(target='{"@@locale":"de","welcome":"Hallo"}'))
    f=next(f for f in w['entries']['welcome']['localFindings'] if f['issue_type']=='placeholder_mismatch')
    w=decision(s,w,'dismissed',findingId=f['id']);assert not s.approval(w,'welcome')
    with pytest.raises(ValueError):decision(s,w)
    assert s.release(w['id'],w['backend']['version'],'validated')['blocked']
    draft=s.release(w['id'],w['backend']['version'],'draft');assert draft['errors'] and not draft['blocked']
    assert next(f for f in json.loads(draft['files']['review_state.json'])['findings'] if f['issue_type']=='placeholder_mismatch')['reviewed_by_human']


def test_release_immutable_snapshots_cas_and_reanalysis(tmp_path):
    s=WorkspaceService(tmp_path);w=s.create(candidate());w=edited(s,w)
    assert w['entries']['welcome']['checkedRevisionId'] is None
    w=s.analyze(w['id'],w['backend']['version'])
    assert w['entries']['welcome']['checkedRevisionId']==w['entries']['welcome']['currentRevisionId']
    w=s.prepare(w['id'],w['backend']['version'],'draft');r=w['releases'][-1]
    assert r['contentFingerprint']==s.fingerprint(w)
    before=copy.deepcopy(r);w=edited(s,w,'Noch anders {name}','draft-3')
    assert w['releases'][-1]==before and before['contentFingerprint']!=s.fingerprint(w)
    assert s.release(w['id'],w['backend']['version'],'draft')['preparedReleaseId'] is None
    immutable=copy.deepcopy(w);immutable['snapshots']['source']['raw']='{}'
    with pytest.raises(ValueError): s.save(w['id'],w['backend']['version'],immutable)
    assert s.verify(w['id'])


def test_missing_arbitrary_keys_duplicates_metadata_and_icu(tmp_path):
    s=WorkspaceService(tmp_path)
    raw='{"@@locale":"de","welcome":"Hallo {name}","welcome":"Willkommen {name}","extra.key":"Behalten"}\n'
    w=s.create(candidate(target=raw)); assert w['entries']['extra.key']['source'] is None
    assert s.release(w['id'],1,'validated')['files']['intl_de_fixed.arb']==raw
    src='{"@@locale":"en","__proto__":"{n, plural, =0{None} other{{n} items}}","@__proto__":{"description":"Count"}}'
    w=s.create(candidate(source=src,target='{"@@locale":"de"}'))
    assert s.release(w['id'],1,'validated')['blocked']
    c=copy.deepcopy(w);c['entries']['__proto__'].update(workingText='{n, plural, =0{Keine} other{{n} Dinge}}',currentRevisionId='icu-1')
    w=s.save(w['id'],1,c);assert not w['entries']['__proto__']['backendValidation']['errors']
    r=s.release(w['id'],w['backend']['version'],'draft');out=json.loads(r['files'][r['filename']])
    assert out['@__proto__']['description']=='Count' and out['@__proto__']['reviewed_by_human'] is False


def test_glossary_is_real_engine_input_and_unknown_columns_survive(tmp_path):
    s=WorkspaceService(tmp_path); c=candidate();c['glossary']=[{'ru':'Союз','kk':'Одақ','en':'Union','status':'agreed','variants':['Одаққа'],'custom':'Retain'}]
    w=s.create(c);assert 'consumer_agreed' in glossary_text(w['glossary'],analysis=True)
    assert 'kk_variants' in glossary_text(w['glossary'],analysis=True)
    c=copy.deepcopy(w);c['glossary'][0]['kk']='Бірлестік';w=s.save(w['id'],1,c)
    assert w['entries']['welcome']['terminologyStale']
    assert not s.release(w['id'],w['backend']['version'],'validated')['coverage']['matches_selected_output']
    assert 'Retain' in s.release(w['id'],w['backend']['version'],'draft')['files']['glossary.csv']


def test_flutter_reports_missing_sdk_truthfully(tmp_path, monkeypatch):
    monkeypatch.setattr('dalel.delivery.flutter.find_flutter',lambda:None)
    s=WorkspaceService(tmp_path);w=s.create(candidate());w=s.flutter(w['id'],1,'validated')
    r=s.release(w['id'],w['backend']['version'],'validated')
    assert r['flutter']['status']=='not_run' and 'SDK not found' in r['flutter']['reason']
    assert json.loads(r['files']['review_state.json'])['structural_validation']['flutter_compilation']==r['flutter']


def test_http_actual_assets_security_import_save_analysis_zip_and_restart(tmp_path):
    service=WorkspaceService(tmp_path,simulation=True)
    try: server=WorkspaceHTTPServer(('127.0.0.1',0),service)
    except PermissionError: pytest.skip('This sandbox prohibits loopback sockets')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    def request(path,body=None,token=None,origin=None):
        headers={'X-Dalel-Token':token or '', 'Content-Type':'application/json'}
        if origin:headers['Origin']=origin
        req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        return urllib.request.urlopen(req)
    try:
        bootstrap=json.load(request('/api/bootstrap'));token=bootstrap['token']
        assert not bootstrap['modelCallsAllowed']
        for asset in ('/','/standalone.html','/styles.css','/src/core.js','/src/backend.js','/src/app.js'):
            assert request(asset).status==200
        for path in ('/api/workspaces','/../service.py'):
            with pytest.raises(urllib.error.HTTPError): request(path)
        with pytest.raises(urllib.error.HTTPError):request('/api/workspaces',{},token,origin='https://evil.invalid')
        w=json.load(request('/api/workspaces',{'workspace':candidate()},token))['workspace']
        path='/api/workspaces/'+w['id']
        with pytest.raises(urllib.error.HTTPError) as error:request(path+'/save',{'workspace':w},token)
        assert error.value.code==422
        w=json.load(request(path+'/analyze',{'expectedVersion':w['backend']['version']},token))['workspace']
        w=json.load(request(path+'/prepare',{'expectedVersion':w['backend']['version'],'mode':'draft'},token))['workspace'];r=w['releases'][-1]
        with zipfile.ZipFile(io.BytesIO(request(path+'/releases/'+r['id']+'/package.zip',token=token).read())) as archive:
            assert set(archive.namelist())==set(r['files'])
            assert archive.read('intl_de_fixed.arb').decode()==candidate()['snapshots']['target']['raw']
        edited_state=copy.deepcopy(w);edited_state['entries']['welcome'].update(workingText='Neu {name}',currentRevisionId='next')
        w=json.load(request(path+'/save',{'expectedVersion':w['backend']['version'],'workspace':edited_state},token))['workspace']
        with pytest.raises(urllib.error.HTTPError) as error:request(path+'/releases/'+r['id']+'/package.zip',token=token)
        assert error.value.code==409
        assert WorkspaceService(tmp_path).get(w['id'])==w
    finally:
        server.shutdown();server.server_close();thread.join()


def test_reusing_analyzed_or_judged_revision_id_cannot_move_evidence(tmp_path):
    s=WorkspaceService(tmp_path);w=s.create(candidate(target='{"@@locale":"de","welcome":"Hallo"}'))
    e=w['entries']['welcome'];old_id=e['currentRevisionId'];old_text=e['workingText']
    fid=e['localFindings'][0]['id'];w=decision(s,w,'dismissed',findingId=fid)
    w=edited(s,w,'Hallo {name}',rid=old_id)
    assert w['entries']['welcome']['currentRevisionId']!=old_id
    assert w['entries']['welcome']['localFindings'][0]['target']==old_text
    with pytest.raises(Conflict):decision(s,w,'dismissed',findingId=fid)
    r=s.release(w['id'],w['backend']['version'],'draft')
    row=next(f for f in json.loads(r['files']['review_state.json'])['findings'] if f['id']==fid)
    assert not row['current_analysis'] and not row['reviewed_by_human']


def test_real_javascript_bridge_and_connected_renderers(tmp_path):
    import shutil
    import subprocess
    node=shutil.which('node')
    if not node: pytest.skip('Node is unavailable')
    s=WorkspaceService(tmp_path,simulation=True)
    try:server=WorkspaceHTTPServer(('127.0.0.1',0),s)
    except PermissionError:pytest.skip('This sandbox prohibits loopback sockets')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        root=Path(__file__).resolve().parents[2]
        result=subprocess.run([node,str(Path(__file__).with_name('connected_client.cjs')),str(root/'src/dalel/assets/workspace'),'http://127.0.0.1:'+str(server.server_port)],capture_output=True,text=True,timeout=60)
        assert result.returncode==0,result.stdout+result.stderr
        assert all(not s.approval(s.current(),k) for k in s.current()['order'])
    finally:
        server.shutdown();server.server_close();thread.join()


def test_null_metadata_retains_editability_and_code_markers_block_release(tmp_path):
    s=WorkspaceService(tmp_path);w=s.create(candidate(target='{"@@locale":"de","welcome":"Hallo {name}","@welcome":null}'))
    w=edited(s,w);r=s.release(w['id'],w['backend']['version'],'draft')
    assert json.loads(r['files'][r['filename']])['@welcome']['reviewed_by_human'] is False
    w=s.manual(w['id'],w['backend']['version'],{'key':'welcome','revisionId':w['entries']['welcome']['currentRevisionId'],'issue_type':'length_overflow','severity':'high','evidence':'Synthetic code dependency','suggestion':'requires_code_change: increase width'})
    w=decision(s,w)
    r=s.release(w['id'],w['backend']['version'],'validated');assert r['blocked'] and r['codeDependencies']==['welcome']
    assert 'requires_code_change' not in json.loads(r['files'][r['filename']])['welcome']
    fid=w['entries']['welcome']['manualFindings'][0]['id'];w=decision(s,w,'dismissed',findingId=fid)
    cleared=s.release(w['id'],w['backend']['version'],'validated')
    assert not cleared['codeDependencies'] and cleared['blocked']


def test_failed_flutter_check_blocks_only_its_exact_output(tmp_path, monkeypatch):
    from dalel.delivery.flutter import FlutterRunner
    monkeypatch.setattr('dalel.delivery.flutter.find_flutter',lambda:Path('/synthetic-sdk/bin/flutter'))
    monkeypatch.setattr(FlutterRunner,'version',lambda self:{'status':'passed'})
    def fail(self,*args,**kwargs):
        log=self.evidence/'failed.log';log.write_text('Synthetic compilation failure')
        return {'generation':'failed','exit_code':1,'log':str(log)}
    monkeypatch.setattr(FlutterRunner,'generate',fail)
    s=WorkspaceService(tmp_path);w=s.create(candidate());w=s.flutter(w['id'],1,'validated')
    r=s.release(w['id'],w['backend']['version'],'validated');assert r['blocked'] and r['flutter']['status']=='failed'
    with pytest.raises(ValueError):s.prepare(w['id'],w['backend']['version'],'validated')
    w=edited(s,w);r=s.release(w['id'],w['backend']['version'],'draft');assert r['flutter']['status']=='not_run'


def test_review_receipts_are_version_bound_and_keep_actor_time(tmp_path):
    from dalel.workspace.decisions import export
    s=WorkspaceService(tmp_path);w=decision(s,edited(s,s.create(candidate())))
    packet=export(w)
    event=packet['entries'][0]
    w=s.import_review(w['id'],w['backend']['version'],packet)
    accepted=s.approval(w,'welcome')
    assert (accepted['reviewer'],accepted['at'])==(event['reviewer'],event['at'])
    w=edited(s,w,'Neue Revision {name}','next-version')
    with pytest.raises(ValueError,match='different text revisions'):
        s.import_review(w['id'],w['backend']['version'],packet)
    assert not s.approval(s.get(w['id']),'welcome')


def test_context_uses_each_ordered_source_comment_and_report_keeps_nine_fields(tmp_path):
    from dalel.contracts import Finding
    source='{"@@locale":"en","@@comment":"First screen","welcome":"Hello {name}","@@comment":"Other screen","other":"Other"}'
    target='{"@@locale":"de","welcome":"Hallo","other":"Andere"}'
    s=WorkspaceService(tmp_path);w=s.create(candidate(source,target))
    assert w['entries']['welcome']['section']=='First screen'
    assert w['entries']['other']['section']=='Other screen'
    assert w['entries']['welcome']['neighbors']==[]
    exported=s.release(w['id'],w['backend']['version'],'draft')
    rows=json.loads(exported['files']['report.json'])
    assert rows and all(set(row)==set(Finding.model_fields) for row in rows)
    assert all(not row['reviewed_by_human'] for row in rows)
    assert 'findings' in json.loads(exported['files']['review_state.json'])


def test_saved_analysis_opens_in_workspace_without_losing_proposals(tmp_path):
    from dalel.delivery.replay import package_report
    from dalel.formats.arb import ArbDocument
    source=ArbDocument(b'{"@@locale":"en","label":"Open"}')
    target=ArbDocument(b'{"@@locale":"de","label":"Zu"}')
    row=dict(key='label',source='Open',target='Zu',suggestion='Auf',issue_type='machine_translation_suspect',
             severity='high',evidence='В «Zu» противоположное действие.',confidence=.9,reviewed_by_human=False)
    bundle=tmp_path/'bundle'
    package_report(source,target,[row],bundle,coverage=dict(status='complete',source_strings_evaluated=1),provenance=dict(producer='external'))
    s=WorkspaceService(tmp_path/'workspaces',simulation=True);w=s.open_bundle(bundle)
    assert w['backend']['analysis']['language_complete'] and w['processing']['externalCalls']==0
    assert w['entries']['label']['importedFindings'][0]['suggestion']=='Auf'
    assert not s.approval(w,'label') and WorkspaceService(tmp_path/'workspaces').current()==w


def test_workspace_flutter_uses_shared_explicit_adapter_without_renaming_saved_inputs(tmp_path, monkeypatch):
    from dalel.delivery.flutter import FlutterRunner
    monkeypatch.setattr('dalel.delivery.flutter.find_flutter',lambda:Path('/synthetic-sdk/bin/flutter'))
    monkeypatch.setattr(FlutterRunner,'version',lambda self:{'status':'passed'})
    generated=[]
    def compile_selection(self,name,files,**options):
        generated.append((name, files))
        key='Upper' if name=='literal' else 'upper'
        assert json.loads(files['intl_en.arb'])[key]=='Open {name}'
        assert json.loads(files['intl_de.arb'])[key]=='Auf {name}'
        log=self.evidence/(name+'-generation.log');log.write_text('Software contract fixture')
        return dict(generation='failed' if name=='literal' else 'passed',exit_code=1 if name=='literal' else 0,log=str(log))
    monkeypatch.setattr(FlutterRunner,'generate',compile_selection)
    s=WorkspaceService(tmp_path)
    c=candidate(source='{"@@locale":"en","Upper":"Open {name}"}',target='{"@@locale":"de","Upper":"Auf {name}"}')
    w=s.create(c);raw=w['snapshots']['target']['raw'];w=s.flutter(w['id'],1,'draft')
    assert generated and w['snapshots']['target']['raw']==raw
    result=w['backend']['flutter'];assert result['adapter']['key_map']=={'Upper':'upper'}
    assert [name for name, files in generated]==['selected','literal']
    assert generated[1][1]['intl_en.arb'].decode()==c['snapshots']['source']['raw']
    assert generated[1][1]['intl_de.arb'].decode()==raw
    assert result['literal_target']['excluded_source_ids']==[]
    assert result['literal_target']['status']=='failed' and result['adapted_bundle']['status']=='passed'
    release=s.release(w['id'],w['backend']['version'],'draft')
    assert release['files']['intl_de_fixed.arb']==raw
    assert json.loads(release['files']['flutter/arb/intl_de.arb'])['upper']=='Auf {name}'
