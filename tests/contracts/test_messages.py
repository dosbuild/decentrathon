import pytest

from dalel.formats.messages import differences,parse,protected,validate_suggestion


def types(source,target,escaping=False):
    return {d['type'] for d in differences(parse(source,escaping),parse(target,escaping))}


def test_repetition_not_a_structural_defect():
    assert not types('{name} {name}','{name}')
    assert not types('{n, plural, one{One} other{Many}}','{n, plural, other{Көп}}')


def test_selector_path_movement_is_a_defect():
    a = '{gender, select, female{{person}} male{{count}} other{n/a}}'
    b = '{gender, select, female{{count}} male{{person}} other{n/a}}'
    assert 'placeholder_mismatch' in types(a,b)
    # Empty branches are still structural paths. A globally present name must
    # not conceal moving a value to a different user-visible selection.
    a = '{g, select, a{{person}} other{None}}'
    b = '{g, select, a{None} other{{person}}}'
    assert 'placeholder_mismatch' in types(a,b)


@pytest.mark.parametrize('text',[
    '{n, plural, one{One}}',
    '{n, plural, invalid{One} other{Many}}',
    '{n, plural, other{Many}',
    '{n, plural, =3{Three} other{Many}}',
])
def test_malformed_plural(text):
    assert 'plural_mismatch' in types('{n, plural, other{Many}}',text)


def test_lost_control_type_and_select_errors():
    assert types('{n, plural, other{Many}}','Many')=={'plural_mismatch'}
    assert types('{n, plural, other{Many}}','{n}')=={'plural_mismatch'}
    assert 'placeholder_mismatch' in types('{g, select, other{Any}}','Any')
    assert 'diagnostic' in types('{g, select, other{Any}}','{g, select, yes{Да}}')


def test_duplicate_selector_matches_flutter_warning():
    result = parse('{n, plural, other{One} other{Many}}')
    assert not result.errors
    assert len(result.warnings)==1


def test_flutter_ignores_tokens_after_unmatched_root_closing_brace():
    result=parse('Stone} {unused}')
    assert not result.errors and not result.names
    assert len(result.warnings)==1


def test_escaping_and_skeletons():
    message = "'{notAnArg}' {when, date, ::yMMMd} {clock, time, ::Hm} {x}"
    assert parse(message,True).names=={'when','clock','x'}
    assert 'notAnArg' in parse(message,False).names
    assert not parse("'{literal}' and '' {value}",True).errors


def test_nested_plural_select_roles():
    text = '{n, plural, =0{None} one{{g, select, a{{who}} other{{who}}}} other{{who}: #}}'
    result = parse(text)
    assert not result.errors
    assert result.names=={'n','g','who'}
    assert ((('n','one'),('g','a')),'who','value') in result.roles


def test_suggestion_contract():
    assert not validate_suggestion('Hello {x}','Сәлем {x}')
    assert validate_suggestion(' Hello {x} ','Сәлем {x}')
    assert validate_suggestion('<b>{x}</b>\ue001','{x}')
    assert len(protected('%% %s %2$d')['positional'])==2
    assert protected('20% discount')['positional']=={}
