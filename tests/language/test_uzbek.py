from dalel.engine.rules import check
from test_production import document


def corpus():
    source={f'entry{i}':f'The game section number {i}' for i in range(12)}
    target={f'entry{i}':f'Oʻyin boʻlimi {i}' for i in range(12)}
    return source,target


def test_uzbek_apostrophe_majority_is_reported_once_per_message_and_preserves_syntax():
    source,target=corpus()
    source['entry11']='The game section {name}'
    target['entry11']="O’yin bo'limi {name}"
    findings,diagnostics=check(document('en',**source),document('uz',**target))
    assert len(findings)==1
    row=findings[0]
    assert row.issue_type=='orthography' and row.suggestion=='Oʻyin boʻlimi {name}'
    policy=next(d for d in diagnostics if d['type']=='uzbek_apostrophe_policy')
    assert policy['dominant']['letter']=='ʻ'
    fixed=target|{'entry11':row.suggestion}
    assert check(document('en',**source),document('uz',**fixed))[0]==[]


def test_uniform_corpus_mark_and_shared_names_are_not_errors():
    source,target=corpus()
    target={k:v.replace('ʻ',"'") for k,v in target.items()}
    source['properName']="Welcome O’Connor"
    target['properName']='OʻConnor, xush kelibsiz'
    target['entry11']+=" https://example.test/o’yin?q=1"
    assert check(document('en',**source),document('uz',**target))[0]==[]


def test_tutuq_and_letter_marks_have_independent_majorities_and_ties_abstain():
    source,target=corpus()
    target={k:v+' maʼno' for k,v in target.items()}
    target['entry11']+=' ma’no'
    findings,_=check(document('en',**source),document('uz',**target))
    assert len(findings)==1 and findings[0].suggestion.endswith('maʼno maʼno')
    target={k:v.replace('ʻ',"'" if i%2 else 'ʻ') for i,(k,v) in enumerate(corpus()[1].items())}
    assert check(document('en',**source),document('uz',**target))[0]==[]


def test_uzbek_mixed_cyrillic_prose_is_detected_without_reference_but_names_survive():
    source=document('en',mixed='Open the window',name='Visit Алматы',code='Use NASA')
    target=document('uz',mixed='Iltimos окно oching',name='Алматы shahriga boring',code='NASA tanlang')
    findings,_=check(source,target)
    assert [(r.key,r.issue_type) for r in findings]==[('mixed','wrong_language')]


def test_uzbek_dynamic_direction_affix_needs_runtime_assimilation_but_constant_cases_do_not():
    source=document('ru',direction='Иди к {place}',object='Найди {item}',location='Жди в {place}')
    target=document('uz',direction='{place}ga boring',object='{item}ni toping',location='{place}da kuting')
    findings,_=check(source,target)
    assert [(r.key,r.issue_type) for r in findings]==[('direction','orthography')]


def test_sparse_lexicon_does_not_turn_common_inflections_or_loans_into_new_meanings():
    from dalel.languages.uzbek import spelling_candidates
    for word in ('oling','olib','olgansiz','haqiqatan','summasi','esda','tayyorladim',
                 'yordamching','qishlogʻida','qoʻyildi','loot','looti'):
        assert not spelling_candidates(word),word


def test_lexicon_proves_four_spelling_classes_without_rewriting_valid_alternatives():
    pairs=(('habar','xabar'),('xayot','hayot'),('samol','shamol'),
           ('mano','maʼno'),('soaat','soat'))
    for bad,good in pairs:
        source=document('en',label='Information for the reader')
        findings,_=check(source,document('uz',label=bad))
        assert [(r.issue_type,r.suggestion) for r in findings]==[('orthography',good)]
        assert check(source,document('uz',label=good))[0]==[]
    # Both words are attested even if one-edit neighbours exist. The spelling
    # detector must leave sense disambiguation to a translation review.
    assert check(document('en',label='At dawn'),document('uz',label='sahar'))[0]==[]
