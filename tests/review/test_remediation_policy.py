from pathlib import Path


def test_unapproved_latin_brand_cannot_force_a_foreign_language_fix(monkeypatch):
    from dalel.terminology.guard import PolicyValidator
    from dalel.engine.context import CorpusPolicy
    term={'ru':'Северный фестиваль','kk':'Northern Festival',
          '_rule':{'status':'ai_proposed','enforce':False}}
    validator=PolicyValidator(CorpusPolicy([]),[term])
    assert validator.requirements('СЕВЕРНЫЙ ФЕСТИВАЛЬ')==[]
    assert validator.requirements('Северный фестиваль / Northern Festival')==[]
    term['_rule'].update(status='human_approved',enforce=True,approval={'reviewer':'Contract fixture','at':'2026-01-01T00:00:00Z','attest_human':True})
    assert len(validator.requirements('СЕВЕРНЫЙ ФЕСТИВАЛЬ'))==1
