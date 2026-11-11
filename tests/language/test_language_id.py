"""Zero-reference generality and precision on independently written UI pairs."""
import pytest
from dalel.engine.rules import check
from test_production import document as doc


@pytest.mark.parametrize('source_locale,target_locale,source,target,kind',[
    ('en','de','Open the settings','Откройте настройки','wrong_language'),
    ('en','es','Close this window','Закройте это окно','wrong_language'),
    ('en','de','Please save your changes','Please save your changes','same_as_source'),
    ('en','es','Press the button to continue','Press the button to continue','same_as_source'),
    ('en','kk','Open settings','Open settings','same_as_source'),
    ('fr','de','Veuillez ouvrir votre fenêtre','Veuillez ouvrir votre fenêtre','same_as_source'),
    ('es','fr','Confirme los cambios para continuar','Confirme los cambios para continuar','same_as_source'),
    ('it','de','Scegli una nuova finestra','Scegli una nuova finestra','same_as_source'),
    ('ru','kk','Откройте настройки','Open settings','wrong_language'),
    ('ru','kk','Пожалуйста, повторите попытку','Iltimos qayta urinib ko‘ring','wrong_language'),
    ('en','kk','Check the mailbox','Pochta qutisini tekshiring','wrong_language'),
    ('de','es','Bitte bestätigen Sie Ihre Auswahl','Please confirm your selection','wrong_language'),
    ('en','de','{n, plural, one{Open the file} other{Open the files}}',
     '{n, plural, one{Откройте файл} other{Откройте файлы}}','wrong_language'),
    ('ru','kk-Latn','Сохраните изменения','Please save your changes','wrong_language'),
])
def test_language_without_reference_files(source_locale,target_locale,source,target,kind):
    findings,_=check(doc(source_locale,message=source),doc(target_locale,message=target))
    assert [r.issue_type for r in findings]==[kind]
    assert findings[0].severity=='high' and not findings[0].reviewed_by_human


@pytest.mark.parametrize('source_locale,target_locale,source,target',[
    ('en','de','Orion Fleet','Orion Fleet'),
    ('ru','kk','НАЧАЛО','STAR TRAIL'),
    ('en','kk','VIP GPS USB','VIP GPS USB'),
    ('en','de','123 AB-456','123 AB-456'),
    ('en','de','{Please} {open} {settings}','{Please} {open} {settings}'),
    ('en','de','https://example.test/please/open/settings','https://example.test/please/open/settings'),
    ('en','de','admin@example.test','admin@example.test'),
    ('en','de','<Please><open>Orion</open></Please>','<Please><open>Orion</open></Please>'),
    ('en','de','{n, plural, one{1} other{#}}','{n, plural, one{1} other{#}}'),
    ('en','de','{event, select, please{1} save{2} other{3}}','{event, select, please{1} save{2} other{3}}'),
    ('en','de','Radio and Internet','Radio und Internet'),
    ('en','de','Save your changes','Bitte speichern Sie Ihre Änderungen'),
    ('en','es','Save your changes','Guarde los cambios'),
    ('en','fr','Save your changes','Veuillez enregistrer vos modifications'),
    ('en','uz','Open the mailbox','Pochta qutisini oching'),
    ('ru','kk-Latn','Откройте настройки','Baptaýlardy ashyńyz'),
    ('ru','kk','Интернет радио такси','Интернет радио такси'),
    ('en','de','Choose Orion Fleet','Orion Fleet auswählen'),
    ('en','de','Orion Fleet ID','Orion Fleet ID'),
    ('en','de','%s %d','%s %d'),
    ('ru','kk','Включите speed meter','Speed meter көрсетілсін'),
    ('ru','kk','Счётчик party-token открыт','Party-token санауышы ашық'),
    ('ru','kk','Названия команд в скобках','Ойында (Hit), келесі амал (Stay), тағы бір амал (Double) бар.'),
    ('ru','kk','Comet engine pack','Comet engine pack'),
])
def test_names_tokens_and_native_prose_are_not_foreign(source_locale,target_locale,source,target):
    findings,_=check(doc(source_locale,message=source),doc(target_locale,message=target))
    assert not findings


def test_contaminated_reference_does_not_establish_language_or_shared_loan():
    source=doc('ru',message='Проверьте дверь',loan='Радио')
    target=doc('uz',message='Eshikni tekshiring',loan='Radio')
    contaminated=doc('kk',message='Eshikni tekshiring')
    english=doc('en',message='Check the door',loan='Radio')
    assert not check(source,target,[contaminated,english])[0]
    # Equality to a credible English sentence still has independent markers.
    target=doc('uz',message='Please check the door',loan='Radio')
    english=doc('en',message='Please check the door',loan='Radio')
    assert [r.issue_type for r in check(source,target,[english])[0]]==['wrong_language']
