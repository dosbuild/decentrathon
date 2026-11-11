from dalel.engine.hygiene import FixValidator,clean_findings,case_only

def finding(**changes):
    value=dict(key='synthetic',issue_type='orthography',severity='medium',source='ПРИМЕР',target='Мысал',
        suggestion='МЫСАЛ',evidence='Регистр «Мысал» отличается от исходника.',confidence=.85,reviewed_by_human=False)
    return value|changes

def test_cross_locale_case_is_diagnostic_but_internal_corruption_is_not_suppressed():
    assert case_only(finding())
    assert not case_only(finding(target='МыСал',suggestion='Мысал',evidence='Повреждён регистр внутри «МыСал».'))
    assert not case_only(finding(target='МысаЛи',suggestion='Мысалы',evidence='Неверная буква и регистр: «МысаЛи».'))
    clean,diagnostics,reject,audit=clean_findings([finding()],FixValidator(['Мысал']))
    assert not clean and len(diagnostics)==1 and not reject

def test_unattested_russian_stem_and_numeric_plural_are_rejected():
    validator=FixValidator(['кітап','кітаптар','балалар','бала','Google'])
    assert any(e['rule']=='unattested_new_token' for e in validator.check('Пример','кітап','библиотекочкасы'))
    assert not validator.check('Google','Google','Google')
    assert any(e['rule']=='plural_after_count' for e in validator.check('3 книги','кітап','3 кітаптар'))
    assert any(e['rule']=='plural_after_count' for e in validator.check('{count} книг','кітап','{count} кітаптар'))
    assert any(e['rule']=='plural_after_count' for e in validator.check('%s книг','кітап','%s кітаптар'))
    assert not validator.check('3 книги','кітап','3 кітап')
    assert any(e['rule']=='plural_after_count' for e in validator.check('Две новые книги','кітап','екі жаңа кітаптар'))
    assert not any(e['rule']=='plural_after_count' for e in validator.check('3 дня прошли, дети пришли','күн','3 күн өтті балалар келді'))
    assert not any(e['rule']=='plural_after_count' for e in validator.check('Трое детей','бала','3 балалар'))
    assert any(e['rule']=='plural_after_count' for e in validator.check('Трое детей и четыре книги','бала','3 балалар және 4 кітаптар'))
    assert not any(e['rule']=='plural_after_count' for e in validator.check('{name} и дети','бала','{name} балалар'))

def test_semantic_evidence_is_retyped_and_unsupported_high_is_rechecked():
    omitted=finding(issue_type='inconsistent_term',target='Ашу',suggestion=None,
                    evidence='Вариант «Ашу» опускает объект действия из источника.')
    cleaned,_,_,_=clean_findings([omitted],FixValidator(['Ашу']))
    assert cleaned[0]['issue_type']=='machine_translation_suspect'
    row=finding(target='Күн',suggestion='Түн',severity='high',evidence='Неверный смысл: «Күн» обозначает другое время суток.')
    cleaned,_,_,audit=clean_findings([row],FixValidator(['Күн','Түн']))
    assert cleaned[0]['issue_type']=='machine_translation_suspect'
    assert any(a['action']=='retyped' for a in audit)
    row=finding(issue_type='machine_translation_suspect',severity='high',target='Мысал',suggestion=None,
                evidence='Уточняющее слово в «Мысал» избыточно.')
    cleaned,_,_,audit=clean_findings([row],FixValidator(['Мысал']))
    assert cleaned[0]['severity']=='medium' and any(a['action']=='severity_rechecked' for a in audit)
    row=finding(issue_type='length_overflow',severity='low',target='Мысал',suggestion=None,
                evidence='Ярлык «ПРИМЕР» заменён инструкцией вместо названия объекта.')
    cleaned,_,_,audit=clean_findings([row],FixValidator(['Мысал']))
    assert cleaned[0]['issue_type']=='machine_translation_suspect'

def test_new_inflected_verbs_need_a_public_canonical_root():
    validator=FixValidator([])
    for word in ('жазып','күлсең','оқиды','тексеріп','келетін','айтасың'):
        assert validator.attested(word),word
    assert not validator.attested('библиотекочкасы')
    assert not validator.attested('печатайсың')

def test_counted_reward_cannot_become_the_duration_of_a_concat_fragment():
    validator=FixValidator(['әрекеттен','кейін','әрекет','минуттан'])
    assert any(e['rule']=='temporal_attachment' for e in
        validator.check('Ещё 3 попытки через','3 әрекет','3 әрекеттен кейін'))
    assert not any(e['rule']=='temporal_attachment' for e in
        validator.check('После 3 попыток','3 әрекет','3 әрекеттен кейін'))
    assert not any(e['rule']=='temporal_attachment' for e in
        validator.check('Через 3 минуты','3 әрекет','3 минуттан кейін'))

def test_count_context_in_original_short_fragment_still_requires_singular():
    validator=FixValidator(['қоян','қояндар','жылқы','жылқылар'])
    assert any(e['rule']=='inherited_count_plural' for e in validator.check('ЗАЙЦА','2 қоян','ҚОЯНДАР'))
    assert not any(e['rule'].endswith('count_plural') for e in validator.check('ЗАЙЦА','2 қоян','ҚОЯН'))
    assert any(e['rule']=='plural_after_count' for e in validator.check('4 лошади','4 жылқылар','4 жылқылар'))
    assert not any(e['rule']=='inherited_count_plural' for e in validator.check('Круглосуточно','10.00-ден 20.00-ге дейін','тәулік бойы'))

def test_numeric_ordinal_hyphen_is_not_confused_with_harmony_affix():
    validator=FixValidator(['орын','орынға','күн','күнге'])
    assert any(e['rule']=='ordinal_separator_lost' for e in validator.check('{n}-е место','{n}-орын','{n} орын'))
    assert not any(e['rule']=='ordinal_separator_lost' for e in validator.check('{n}-е место','{n}-орын','{n}-орын'))
    assert any(e['rule']=='ordinal_separator_lost' for e in validator.check('3-й день','3-күн','3 күн'))
    assert not any(e['rule']=='ordinal_separator_lost' for e in validator.check('К {n}','{n}-ге','Мәні: {n}'))
    assert any(e['rule']=='ordinal_separator_lost' for e in validator.check('{rank}-й ряд','{rank}-қатар','{rank} қатар'))
    assert any(e['rule']=='ordinal_separator_lost' for e in validator.check('{rank}-й ряд','{rank} қатар','{rank} қатарға'))
    assert not any(e['rule']=='ordinal_separator_lost' for e in validator.check('4-й ряд','3-қатар','4-қатар'))

def test_runtime_postpositions_fragments_and_source_copy_never_enter_a_fix():
    validator=FixValidator([])
    for word in ('кейін','соң','қарсы','дейін','шейін'):
        assert any(e['rule']=='runtime_postposition_case' for e in validator.check('Пример {person}','Мысал {person}','{person} '+word))
    for text in ('{person} ойыншысына қарсы','{time} уақытынан кейін','Басталуына қалды: {time}'):
        assert not any(e['rule']=='runtime_postposition_case' for e in validator.check('Пример','Мысал',text))
    for source in ('Подарок для','Выполнить до:','После','За','Откроется через: '):
        assert any(e['rule']=='appended_runtime_value_requires_code_change' for e in validator.check(source,'Мысал','Кейін'))
    assert not any(e['rule']=='appended_runtime_value_requires_code_change' for e in validator.check('После запуска','Мысал','Іске қосылған соң'))
    assert any(e['rule']=='source_identical_fix' for e in validator.check('Файл','Кітап','Файл'))
    assert not validator.check('Файл','Кітап','requires_code_change: Передайте аргумент в шаблон.')

def test_count_comparison_retains_ablative_relation_or_a_case_bearing_noun():
    validator=FixValidator(['артық','мәнінен','көп'])
    assert any(e['rule']=='comparison_case_lost' for e in validator.check('Больше {n}','{n}-ден артық','{n} артық'))
    assert not any(e['rule']=='comparison_case_lost' for e in validator.check('Больше {n}','{n}-ден артық','{n} мәнінен артық'))
    assert not any(e['rule']=='comparison_case_lost' for e in validator.check('Больше 5','5-тен көп','5-тен артық'))

def test_real_correction_preserves_caps_source_lines_and_short_ui_shape():
    validator=FixValidator(['кітап','кітаптар','аш','жақсы'])
    errors=validator.check('КНИГА\n{n}','Кітап {n}','Кітап {n}')
    assert {e['rule'] for e in errors}>={'source_caps_lost','target_layout'}
    assert not validator.check('Книга\n{n}','КІТАП {n}','КІТАП\n{n}')
    assert any(e['rule']=='short_label_expansion_needs_review' for e in
        validator.check('Книга','Кітап','Жақсы кітап жақсы кітап жақсы кітап'))

def test_russian_root_claim_cannot_hide_as_an_ai_glossary_preference():
    validator=FixValidator([])
    issues=[dict(issue_type='glossary_violation',severity='medium',
        evidence='Русский корень «библиотекочкасы» с казахским аффиксом оставлен в переводе.')]
    result=validator.original_issues('Откройте библиотеку','библиотекочкасы',issues)
    assert len(result)==1 and result[0]['issue_type']=='machine_translation_suspect'

def test_existing_russian_text_gets_one_primary_card_and_loans_are_not_defects():
    text='Пожалуйста откройте новое окно'
    rows=[finding(source=text,target=text,suggestion=None,issue_type=kind,severity='high',
        evidence='Русский текст «Пожалуйста откройте новое окно» оставлен без перевода.')
        for kind in ('untranslated','wrong_language','same_as_source')]
    clean,diagnostics,_,audit=clean_findings(rows,FixValidator([]))
    assert len(clean)==1 and clean[0]['issue_type']=='same_as_source'
    assert len([r for r in diagnostics if r['type']=='redundant_finding'])==2
    loan=finding(source='Гараж',target='Гараж',suggestion=None,issue_type='same_as_source')
    assert not clean_findings([loan],FixValidator([]))[0]
    shared=finding(source='Компас',target='Компас',suggestion=None,issue_type='same_as_source')
    assert not clean_findings([shared],FixValidator([]))[0]
    assert not FixValidator([]).original_issues('Компас','Компас',[{'issue_type':'same_as_source','severity':'high','evidence':'Текст совпадает.'}])
    mixed=finding(source='Прочитайте письмо',target='Хатты прочитайте',suggestion=None,issue_type='wrong_language',
        evidence='Русское слово «прочитайте» не переведено в казахском предложении.')
    assert clean_findings([mixed],FixValidator([]))[0][0]['issue_type']=='machine_translation_suspect'
    short=finding(source='Городские забеги',target='Городские забеги',suggestion=None,issue_type='same_as_source')
    assert clean_findings([short],FixValidator([]))[0][0]['issue_type']=='same_as_source'

def test_measured_spelling_proof_survives_mixed_script_boilerplate():
    row=finding(source='Пример книги',target='Китап',suggestion='Кітап',
        evidence='Написание «Китап» нарушает правило: казахская основа либо смешение алфавитов.')
    clean,diagnostics,_,_=clean_findings([row],FixValidator(['Кітап']))
    assert len(clean)==1 and clean[0]['issue_type']=='orthography' and not diagnostics

def test_false_claim_that_a_kazakh_letter_is_absent_is_not_publishable_evidence():
    row=finding(target='Мың',suggestion=None,evidence='В казахском алфавите нет буквы «ң».')
    clean,diagnostics,_,_=clean_findings([row],FixValidator([]))
    assert not clean and diagnostics[0]['type']=='false_alphabet_claim'
