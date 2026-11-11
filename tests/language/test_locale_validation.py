from dalel.languages.validation import LocaleFixValidator


def test_uzbek_corrections_keep_valid_case_endings_and_protect_structure():
    validator=LocaleFixValidator('en','uz',['Oʻyin boʻlimi maʼno']*10)
    assert validator.check('Find {item}','{item}ni topin','{item}ni toping')==[]
    assert validator.check('Wait in {place}','{place}da kut','{place}da kuting')==[]
    assert any(e['rule']=='protected_structure' for e in validator.check('Find {item}','{item}ni topin','Toping'))
    assert any(e['rule']=='uzbek_orthography' for e in validator.check('Game section','Oʻyin boʻlimi',"O'yin bo'limi"))
    assert any(e['rule']=='source_identical_fix' for e in validator.check('Save','Wrong','Save'))
    assert any(e['rule']=='wrong_target_language' for e in validator.check('Open the window','Oching','Iltimos окно oching'))
