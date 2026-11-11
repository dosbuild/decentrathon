import json
from types import SimpleNamespace
import pytest
from dalel.formats.arb import ArbDocument
from dalel.engine.prompts import TYPES
from dalel.engine.pipeline import analyze,AnalysisSettings
from dalel.inference.client import BudgetError

def document(locale,**values):
    return ArbDocument(json.dumps({'@@locale':locale,**values},ensure_ascii=False).encode())

class ModelFixture:
    target_locale='de'
    wrong='Schließe {item}'
    right='Öffne {item}'
    def __init__(self,*,answer='yes',truncated=False,fail=False):
        self.config=SimpleNamespace(model='synthetic-model',base_url='https://synthetic.invalid/v1')
        self.requests=[];self.systems=[];self.answer=answer;self.truncated=truncated;self.fail=fail
    def accounting(self,group=None):
        return {'known':len(self.requests)*.001,'reserved':0.,'accounted':len(self.requests)*.001}
    def summary(self,group=None):
        return {'calls':len(self.requests),'accounted_usd':len(self.requests)*.001}
    def call_with_retry(self,messages,**options):
        request=json.loads(messages[-1]['content']);self.requests.append((request,options))
        self.systems.append(messages[0]['content'])
        assert request['source_locale']=='en' and request['target_locale']==self.target_locale
        if self.fail:
            raise BudgetError('synthetic exhausted budget')
        rows=[]
        for row in request['assigned']:
            if 'variants' in row:
                variants=[];preferred=None
                for label,text in row['variants'].items():
                    wrong=text==self.wrong
                    variants.append({'label':label,'issues':[self.issue()] if wrong else [],
                        'negative_types':[t for t in TYPES if not wrong or t!='machine_translation_suspect'],
                        'rationale':'Значение действия проверено.','quote':text})
                    if not wrong:
                        preferred=label
                rows.append({'key':row['key'],'variants':variants,'preferred':preferred,
                    'strict_improvement':self.answer=='yes','improvement_verdict':self.answer,
                    'comparison':'Исправлено направление действия.','requires_code_change':False})
            else:
                wrong=row.get('target')==self.wrong
                rows.append({'key':row['key'],'issues':[dict(self.issue(),suggestion=self.right)] if wrong else [],
                    'negative_codes':''.join(format(i,'x') for i,t in enumerate(TYPES) if not wrong or t!='machine_translation_suspect'),
                    'negative_rationale':'Остальные типы проверены.','negative_quote':row.get('target') or row.get('source'),
                    'needs_review':False})
        finish='length' if self.truncated and len(request['assigned'])>1 else 'stop'
        return {'id':str(len(self.requests)),'cost':.001,'cache_hit':False,'latency':.01,
            'response':{'choices':[{'finish_reason':finish,'message':{'content':json.dumps({'rows':rows},ensure_ascii=False)}}]}}

    def issue(self):
        return {'issue_type':'machine_translation_suspect','severity':'high',
            'evidence':f'В «{self.wrong}» закрытие вместо открытия из исходника: противоположное действие.'}

def issue():
    return {'issue_type':'machine_translation_suspect','severity':'high',
            'evidence':'В «Schließe {item}» закрытие вместо открытия из исходника: противоположное действие.'}

def inputs():
    return document('en',entry='Open {item}',help='Help'),document('de',entry='Schließe {item}',help='Hilfe')


@pytest.mark.parametrize('global_contract',[False,True])
def test_verification_keeps_relevant_message_context_and_global_contracts(tmp_path,global_contract):
    context={'entry':{'meaning':'open, preserve argument'},'help':{'unrelated':'x'*20000}}
    if global_contract:context={'game_rules':context,'entities':{'item':'inventory object'}}
    provider=ModelFixture()
    result=analyze(*inputs(),provider,game_context=context,cache_dir=tmp_path)
    assert result['coverage']['status']=='complete'
    detection,verification=[r for r,options in provider.requests]
    assert detection['game_context']==context
    assert verification['game_context']==(context if global_contract else {'entry':context['entry']})


def test_coarse_forecast_does_not_block_replaying_work(tmp_path):
    provider=ModelFixture()
    provider.projection=lambda forecast: {'allowed':False,'forecast':forecast}
    result=analyze(*inputs(),provider,cache_dir=tmp_path)
    assert result['coverage']['status']=='complete' and not result['errors']
    assert result['projections'] and all(not p['allowed'] for p in result['projections'])

def test_external_locale_full_contract_safe_fix_and_zero_call_replay(tmp_path):
    source,target=inputs();provider=ModelFixture();reference=document('fr',help='Aide')
    result=analyze(source,target,provider,references=[reference],cache_dir=tmp_path)
    assert result['coverage']['status']=='complete' and result['coverage']['typed_decisions']==22
    assert result['report'][0]['suggestion']=='Öffne {item}' and not result['report'][0]['reviewed_by_human']
    assert len(provider.requests)==2 and result['known_paid_response_cost']==pytest.approx(.002)
    provider.fail=True
    replay=analyze(source,target,provider,references=[reference],cache_dir=tmp_path)
    assert replay['replay'] and replay['known_paid_response_cost']==0 and len(provider.requests)==2
    assert replay['report']==result['report']

@pytest.mark.parametrize('answer',['no','unsure'])
def test_confirmed_defect_survives_declined_fix(tmp_path,answer):
    result=analyze(*inputs(),ModelFixture(answer=answer),cache_dir=tmp_path)
    assert len(result['report'])==1 and result['report'][0]['suggestion'] is None
    assert result['coverage']['status']=='complete'

def test_truncation_resplits_and_counts_the_rejected_response_cost(tmp_path):
    provider=ModelFixture(truncated=True)
    result=analyze(*inputs(),provider,cache_dir=tmp_path)
    assert result['coverage']['status']=='complete' and len(provider.requests)==4
    assert result['known_paid_response_cost']==pytest.approx(.004)
    assert result['coverage']['detected_strings']==2

def test_budget_failure_is_partial_and_cache_changes_never_inherit_old_results(tmp_path):
    provider=ModelFixture(fail=True)
    result=analyze(*inputs(),provider,cache_dir=tmp_path)
    assert result['coverage']['status']=='partial' and result['coverage']['detected_strings']==0
    assert len(provider.requests)==1
    provider.fail=False
    complete=analyze(*inputs(),provider,cache_dir=tmp_path)
    assert complete['coverage']['status']=='complete'
    source,target=inputs();changed=document('en',entry='Open the {item}',help='Help')
    new=analyze(changed,target,provider,cache_dir=tmp_path)
    assert new['identity']!=complete['identity'] and not new['replay']
    cache=tmp_path/(new['identity']+'.json');payload=json.loads(cache.read_text(encoding='utf8'))
    payload['report'][0]['suggestion']='Schließe {item}'
    cache.write_text(json.dumps(payload),encoding='utf8')
    with pytest.raises(ValueError,match='checksum'):
        analyze(changed,target,provider,cache_dir=tmp_path)

def test_production_limits_and_prompt_have_no_teacher_answers(tmp_path):
    with pytest.raises(ValueError):
        AnalysisSettings(workers=0)
    provider=ModelFixture();analyze(*inputs(),provider,cache_dir=tmp_path)
    for request,options in provider.requests:
        assert 'teacher' not in json.dumps(request).casefold()
        assert options['budget_group']=='student' and options['group_budget']==15


def test_admission_pause_keeps_partial_coverage_and_reports_progress(tmp_path):
    events=[];provider=ModelFixture()
    result=analyze(*inputs(),provider,cache_dir=tmp_path,settings=AnalysisSettings(workers=1,batch_size=1),
        progress=events.append,continue_run=lambda:not events)
    assert len(provider.requests)==1 and events[0]['completed']==1
    assert result['coverage']['status']=='partial' and result['coverage']['detected_strings']==1
    assert any(e['error']=='AdmissionPaused' for e in result['errors'])


def test_complete_batch_with_one_invalid_row_reuses_every_other_valid_decision(tmp_path):
    class OneBadQuotation(ModelFixture):
        def call_with_retry(self,messages,**options):
            result=super().call_with_retry(messages,**options)
            if len(self.requests)==1:
                payload=json.loads(result['response']['choices'][0]['message']['content'])
                payload['rows'][1]['negative_quote']='fabricated quote'
                result['response']['choices'][0]['message']['content']=json.dumps(payload)
            return result
    provider=OneBadQuotation();result=analyze(*inputs(),provider,cache_dir=tmp_path)
    assert result['coverage']['status']=='complete' and len(provider.requests)==3
    assert [r['key'] for r in provider.requests[1][0]['assigned']]==['help']


def test_complete_row_array_uses_the_same_full_validation_contract(tmp_path):
    class RowArray(ModelFixture):
        def call_with_retry(self,messages,**options):
            result=super().call_with_retry(messages,**options);message=result['response']['choices'][0]['message']
            message['content']=json.dumps(json.loads(message['content'])['rows'])
            return result
    provider=RowArray();result=analyze(*inputs(),provider,cache_dir=tmp_path)
    assert result['coverage']['status']=='complete' and len(provider.requests)==2

def test_uzbek_publishes_independently_verified_ai_findings_without_claiming_native_review(tmp_path):
    class UzbekFixture(ModelFixture):
        target_locale='uz'
        wrong='{item} ni yoping'
        right='{item} ni oching'
    provider=UzbekFixture()
    result=analyze(document('en',entry='Open {item}'),document('uz',entry=provider.wrong),provider,cache_dir=tmp_path)
    assert result['coverage']['status']=='complete' and result['coverage']['calibration']=='uncalibrated'
    assert len(result['report'])==len(result['fixes'])==len(result['report_auto'])==1
    assert result['coverage']['language_candidates']==1 and result['coverage']['uz_reviewer'] is None
    candidate=next(d for d in result['diagnostics'] if d['type']=='uzbek_independent_ai_review')
    assert candidate['independently_confirmed'] and candidate['finding']['suggestion']==provider.right
    assert not candidate['reviewed_by_human'] and not candidate['finding']['reviewed_by_human']
    assert candidate['review_stage']=='ai_only'
    assert all('reviewing Uzbek localization' in system for system in provider.systems)
