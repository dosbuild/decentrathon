from dalel.terminology.canon import audit
from test_production import document


def test_invalid_majority_cannot_elect_a_canon_and_unknowns_are_counted():
    source=document('ru',a='Книга',b='Книга',c='Книга',d='Откройте книгу')
    target=document('kk',a='Терезе',b='Терезе',c='Кітап',d='Белгісіз')
    terms=[{'ru':'Книга','kk':'Кітап','_rule':{'status':'ai_proposed','enforce':False,'source_forms':['книгу']}}]
    result=audit(source,target,terms,valid_keys={'c'})
    term=result['terms'][0]
    assert term['canonical']=='Кітап' and term['selection_basis']=='majority_of_attested_valid_usages'
    assert result['summary']['terms_with_multiple_renderings']==1
    assert result['summary']['unmatched_usages']==1 and term['noncanonical_usages']==3


def test_casing_and_inflections_share_a_term_and_approved_canon_wins():
    source=document('ru',a='Книга',b='Книга',c='Книга')
    target=document('kk',a='КІТАП',b='кітаптар',c='Кітап')
    terms=[{'ru':'Книга','kk':'кітап'}]
    result=audit(source,target,terms,valid_keys={'a','b','c'})
    assert result['summary']['terms_with_multiple_renderings']==0
    assert result['terms'][0]['selection_basis']=='approved_glossary_or_binding_style'


def test_loan_inflection_is_not_a_variant_and_derivation_is_not_inflection():
    from dalel.terminology.canon import equivalent
    assert equivalent('дуэль','дуэльдерде','kk')
    assert equivalent('жинақ сандығы','жинақ сандығымен','kk')
    assert equivalent('карта сыйлығы','карта сыйлықтары','kk')
    assert equivalent('ықпал ұпайы','ықпал ұпайларың','kk')
    assert not equivalent('аңыз','аңыздық','kk')
    source=document('ru',a='Книга',b='Книга',c='Книга')
    target=document('kk',a='Книжечка',b='Книжечка',c='Кітап')
    terms=[{'ru':'Книга','kk':'Кітап','_rule':{'status':'ai_proposed','enforce':False}}]
    result=audit(source,target,terms,valid_keys={'a','b','c'})
    assert result['terms'][0]['canonical']=='Кітап'


def test_permitted_loan_does_not_overrule_valid_native_majority():
    source=document('ru',a='Самолет',b='Самолет',c='Самолет')
    target=document('kk',a='Ұшақ',b='Ұшақ',c='Самолет')
    terms=[{'ru':'Самолет','kk':'Самолет','_rule':{'status':'ai_proposed','enforce':False}}]
    term=audit(source,target,terms,valid_keys={'a','b','c'})['terms'][0]
    assert term['canonical']=='Ұшақ'
    assert term['selection_basis']=='majority_of_attested_valid_usages'
