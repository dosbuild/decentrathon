from dalel.engine.context import CorpusPolicy
from dalel.engine.hygiene import FixValidator,clean_findings

def finding(**changes):
    return dict(key='sampleEntry',source='Подпись',target='Қолтаңба',suggestion='Қол қою',
        issue_type='inconsistent_term',severity='medium',evidence='В корпусе другой вариант: «Қолтаңба».',
        confidence=.85,reviewed_by_human=False)|changes

def test_synonym_unification_is_diagnostic_but_tagged_terms_and_wrong_meaning_are_distinct():
    record=finding();policy=CorpusPolicy([record]);validator=FixValidator(context=policy)
    assert policy.route(record)['type']=='terminology_unification'
    tagged=finding(source='<g>Панель</g>',target='<g>Тақта</g>',suggestion=None,evidence='Вариант термина «Тақта» отличается от названия функции.')
    assert policy.route(tagged) is None
    described=CorpusPolicy([dict(record,metadata={'description':'Item name displayed in the catalog.'})])
    assert described.route(record) is None
    semantic=finding(evidence='Неверный смысл: «Қолтаңба» обозначает другое действие.')
    clean,diagnostics,_,_=clean_findings([semantic],validator)
    assert not diagnostics and clean[0]['issue_type']=='machine_translation_suspect'
    assert policy.route(finding(target='Қолтаңба',suggestion='ҚОЛТАҢБА'))['type']=='terminology_case'


def test_explicit_meaning_or_predicate_defects_are_not_style_preferences():
    examples=[finding(source='Остановить турбину',target='Турбинаны іске қосу',suggestion=None,
        issue_type='glossary_violation',evidence='«іске қосу» означает запуск: противоположное действие и нарушение глоссария.'),
        finding(source='Острова',target='Аралда',suggestion=None,issue_type='machine_translation_suspect',
        evidence='Источник множественного числа, а «Аралда» имеет местный падеж единственного числа: смысл искажён.'),
        finding(source='Затем сообщите диспетчеру.',target='Содан кейін диспетчерге хабарлау.',suggestion=None,
        issue_type='machine_translation_suspect',evidence='«хабарлау» — инфинитив вместо императива: неполный предикат, а не предпочтение регистра.')]
    for row in examples:
        validator=FixValidator(context=CorpusPolicy([row]))
        kept,diagnostics,_,_=clean_findings([row],validator)
        assert not diagnostics and kept[0]['issue_type']=='machine_translation_suspect'


def test_verified_scope_canon_can_cover_long_messages_without_human_approval():
    record=finding(source='Нажмите кнопку Панель для просмотра выбранных документов',target='Тақтаны аш',suggestion=None)
    term={'ru':'Панель','kk':'Терезе','_rule':{'status':'ai_proposed','canon_verification':{'verdict':'yes'}}}
    assert CorpusPolicy([record],canon_terms=[term]).route(record) is None
    term['_rule']['canon_verification']['verdict']='unsure'
    assert CorpusPolicy([record],canon_terms=[term]).route(record)['type']=='terminology_unification'

def test_untranslated_corpus_tokens_cannot_attest_new_russian_words():
    rows=[{'key':'copied','source':'библиотекочка','target':'библиотекочка'},
          {'key':'foreign','source':'Название','target':'инвентаризацияшка'},
          {'key':'clean','source':'Книга','target':'кітап'}]
    policy=CorpusPolicy(rows,[{'key':'foreign','issue_type':'wrong_language'}])
    validator=FixValidator(context=policy)
    assert not validator.frequencies['библиотекочка'] and not validator.frequencies['инвентаризацияшка']
    errors=validator.check('Название','кітап','библиотекочка')
    assert errors==[{'rule':'excluded_corpus_attestation','token':'библиотекочка'}]
    assert validator.attested('кітап')

def test_new_loan_coinage_loses_to_native_form_of_same_source_concept():
    policy=CorpusPolicy([{'key':'native','source':'Рассказ','target':'Әңгіме'},
                        {'key':'loan','source':'Бонус','target':'Сыйақы'},
                        {'key':'unflagged','source':'Пример текста','target':'рассказдар'}])
    assert any(e['rule']=='native_corpus_equivalent' for e in policy.fix_errors('Рассказ','мәтін','рассказдар'))
    assert any(e['rule']=='native_corpus_equivalent' for e in policy.fix_errors('Рассказ','рассказдар','рассказдар'))
    assert not policy.fix_errors('Бонус','қосымша','бонустар')
    assert not policy.fix_errors('Бонус и радио','қосымша','радио')

def test_wrong_meaning_native_word_cannot_attest_a_translation_of_the_source_concept():
    rows=[{'key':'wrongConcept','source':'Бонус','target':'Жапырақ'}]
    policy=CorpusPolicy(rows,[{'key':'wrongConcept','issue_type':'machine_translation_suspect'}])
    assert policy.frequencies['жапырақ']==1
    assert not policy.native_terms.get('бонус')
    assert not policy.fix_errors('Бонус','қосымша','бонустар')
    tagged=CorpusPolicy([{'key':'wrongTag','source':'<g>Бонус</g>','target':'<g>Жапырақ</g>'}],
                        [{'key':'wrongTag','issue_type':'machine_translation_suspect'}])
    assert not tagged.allowed_tag_forms('Бонус')

def test_tag_content_requires_glossary_or_unique_corpus_majority():
    rows=[{'key':'labelA','source':'Нажми <g>Панель</g>','target':'<g>Тақта</g> түймесін бас'},
          {'key':'labelB','source':'Открой <g>Панель</g>','target':'<g>Тақта</g> аш'}]
    policy=CorpusPolicy(rows)
    assert policy.fix_errors(rows[0]['source'],rows[0]['target'],'<g>Терезе</g> түймесін бас')[0]['rule']=='tag_term_rename'
    assert not policy.fix_errors(rows[0]['source'],'<g>Өріс</g> аш','<g>Тақта</g> аш')
    approved=CorpusPolicy(rows,glossary={'Панель':'Терезе'})
    assert not approved.fix_errors(rows[0]['source'],rows[0]['target'],'<g>Терезе</g> түймесін бас')
    assert not approved.fix_errors(rows[0]['source'],rows[0]['target'],'<g>ТЕРЕЗЕ</g> түймесін бас')
    assert not approved.fix_errors(rows[0]['source'],rows[0]['target'],'<g>Терезеде</g> түймесін бас')
    assert any(e['rule']=='tag_term_rename' for e in approved.fix_errors(rows[0]['source'],rows[0]['target'],'<g>Жаңа терезе</g> түймесін бас'))


def test_literal_argument_repair_inside_tag_does_not_rename_its_term():
    policy=CorpusPolicy([{'key':'tagCount','source':'<g>{amount} страниц</g>','target':'<g>amount бет</g>'}])
    assert not policy.fix_errors('<g>{amount} страниц</g>','<g>amount бет</g>','<g>{amount} бет</g>')
    assert any(e['rule']=='tag_term_rename' for e in policy.fix_errors('<g>{amount} страниц</g>','<g>amount бет</g>','<g>{other} бет</g>'))

def test_empty_fragments_and_valid_inflected_loans_are_not_language_defects():
    rows=[{'key':'sampleEntryPrefix','source':'Для','target':''},
          {'key':'sampleEntrySuffix','source':'покупки','target':'сатып алу үшін'}]
    policy=CorpusPolicy(rows)
    record=finding(key='sampleEntryPrefix',source='Для',target='',suggestion='Үшін',issue_type='untranslated')
    assert policy.route(record)['type']=='empty_fragment'
    assert policy.route(record|{'key':'unrelated','source':'Параметры'}) is None
    assert policy.route(finding(issue_type='wrong_language',target='гараждар'))['type']=='valid_kazakh_loan_or_word'

def test_register_requires_internal_mixing_or_evidenced_family_contradiction():
    record=finding(issue_type='machine_translation_suspect',target='Сен кел',suggestion='Сіз келіңіз',
                   evidence='«Сен кел» следует заменить вежливым обращением.')
    assert CorpusPolicy([record]).route(record)['type']=='register_preference'
    assert CorpusPolicy([record]).route(record|{'target':'Сен келіңіз'}) is None
    rows=[{'key':f'screenProfileStep{i}','source':'Инструкция','target':'Сен кел'} for i in range(4)]
    different=record|{'key':'screenProfileStep4','target':'Сіз келіңіз'}
    policy=CorpusPolicy(rows+[different])
    assert policy.route(different) is None
    assert any(e['rule']=='family_register' for e in policy.fix_errors('Инструкция','Сен кел','Сіз келіңіз',key='screenProfileStep5'))
    assert not policy.fix_errors('Инструкция','Сіз келіңіз','Сен кел',key='screenProfileStep5')
    assert not policy.fix_errors('Инструкция','Сен кел','Сіз келіңіз',key='otherSettingsStep')
    assert any(e['rule']=='mixed_register' for e in policy.fix_errors('Инструкция','Сен кел','Сен келіңіз'))

def test_generic_singular_is_not_an_error_from_russian_plural_alone():
    row=finding(source='Читать книги',target='Кітап оқу',suggestion=None,issue_type='machine_translation_suspect',
        evidence='В «Кітап оқу» единственное число вместо множественного в исходнике.')
    policy=CorpusPolicy([row])
    assert policy.route(row)['type']=='kazakh_generic_singular'
    assert policy.route(row|{'source':'Прочитать три книги'}) is None
    assert policy.route(row|{'evidence':row['evidence']+' Это заголовок вкладки коллекции.'}) is None
    assert policy.route(row|{'evidence':'В «Кітап оқу» ед. ч. вместо мн.ч.'})['type']=='kazakh_generic_singular'


def test_short_ui_grammar_preferences_need_context_and_do_not_hide_real_meaning_changes():
    row=finding(source='Добавь задачу',target='Тапсырма қосу',suggestion='Тапсырма қос',
        issue_type='machine_translation_suspect',evidence='В «Тапсырма қосу» инфинитив вместо императива.')
    assert CorpusPolicy([row]).route(row)['type']=='short_ui_action_noun_needs_context'
    assert CorpusPolicy([dict(row,metadata={'description':'Tutorial instruction'})]).route(row) is None
    assert CorpusPolicy([row]).route(row|{'source':'Добавь задачу!'}) is None
    assert CorpusPolicy([row]).route(row,semantic=True) is None
    noun=finding(source='Книга',target='Кітабы',suggestion='Кітап',issue_type='machine_translation_suspect',
        evidence='В «Кітабы» добавлена притяжательность.')
    assert CorpusPolicy([noun]).route(noun,semantic=True)['type']=='short_ui_possessive_needs_context'
    assert CorpusPolicy([noun]).route(noun|{'source':'Моя книга'},semantic=True) is None
    assert CorpusPolicy([noun]).route(noun|{'suggestion':'Терезе'},semantic=True) is None


def test_explicit_informal_source_and_original_override_unrelated_family_majority():
    rows=[{'key':f'gameIntroStep{i}','source':'Инструкция','target':'Сіз келіңіз'} for i in range(4)]
    policy=CorpusPolicy(rows)
    assert any(e['rule']=='explicit_source_register' for e in
        policy.fix_errors('Ты придёшь','Сен келесің','Сіз келіңіз',key='gameIntroStep5'))
    assert not policy.fix_errors('Ты придёшь','Сен келесің','Сен келесің',key='gameIntroStep5')


def test_omitted_object_is_not_a_terminology_inflection_preference():
    row=finding(source='Открыть первый этаж маяка?',target='Бірінші қабатты ашу керек пе?',
        suggestion='Маяктың бірінші қабатын ашу керек пе?',issue_type='inconsistent_term',
        evidence='Перевод «Бірінші қабатты ашу керек пе?» опускает объект из источника.')
    validator=FixValidator(context=CorpusPolicy([row]))
    kept,diagnostics,_,_=clean_findings([row],validator)
    assert not diagnostics and kept[0]['issue_type']=='machine_translation_suspect'
