"""Small offline language signals for UI prose, with explicit abstention.

The inventories are ordinary function words and UI verbs, constructed by
language, not from application keys or corpus answers. Script alone cannot
identify names, codes or one-word loans. ICU syntax is never language text.
"""
import re
import unicodedata
from dalel.formats.messages import parse, POSITIONAL

WORDS=re.compile(r"[^\W\d_]+(?:['’‘ʻʼ][^\W\d_]+)*",re.UNICODE)
FUNCTION={
    'en':set('the and your you are is for with please from into without this that to of not can will has have'.split()),
    'de':set('der die das und bitte ihre ihren deiner dein nicht mit für zum zur aus wird werden'.split()),
    'es':set('el los las una usted sus para por favor que con sin puede tus tu'.split()),
    'fr':set('les des une vous votre vos est pour avec veuillez dans sur pas'.split()),
    'pt':set('dos das para você sua seu será uma não novamente por favor'.split()),
    'uz':set('uchun bilan iltimos siz sizning emas mumkin qayta kerak yoki ham va'.split()),
    'ru':set('пожалуйста ваш ваша ваши чтобы если для или при через без после перед'.split()),
}
UI={
    'en':set('click tap press save open close select continue cancel confirm delete enter retry settings password account changes'.split()),
    'de':set('speichern öffnen schließen wählen auswählen fortfahren abbrechen bestätigen löschen eingeben einstellungen passwort'.split()),
    'es':set('guardar abrir cerrar elegir continuar cancelar confirmar eliminar contraseña ajustes'.split()),
    'fr':set('enregistrer ouvrir fermer choisir continuer annuler confirmer supprimer mot passe paramètres'.split()),
    'pt':set('salvar abrir fechar escolher continuar cancelar confirmar excluir senha configurações'.split()),
    'uz':set('ochish yopish tanlang bosing saqlash saqlang tekshiring davom bekor hisob parol sozlamalar'.split()),
    'ru':set('нажмите откройте закройте выберите сохраните подтвердите удалите продолжить настройки пароль'.split()),
}
SHARED=set('vip premium gps usb wifi bluetooth internet radio taxi menu video audio id ok xp hp mp fps afk ui max min km kg usd eur kzt'.split())
LATIN=set('en de es fr pt it nl sv no nb nn da fi et lv lt pl cs sk sl hr ro hu tr az uz id ms vi sw sq ca eu cy ga is mt'.split())
CYRILLIC=set('ru kk ky uk bg be mk sr tg mn'.split())


def base(locale):return locale.replace('_','-').split('-')[0].lower()


def script(locale):
    parts=locale.replace('_','-').lower().split('-')
    if 'latn' in parts:return 'LATIN'
    if 'cyrl' in parts:return 'CYRILLIC'
    if parts[0] in LATIN:return 'LATIN'
    if parts[0] in CYRILLIC:return 'CYRILLIC'
    return None


def prose(text,escaping=False,message=None):
    message=message if message is not None else parse(text,escaping)
    value=' '.join(text[start:end] for start,end in message.text_spans)
    value=re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+|<[^>]*>|\b\w*[\d_]\w*\b',' ',value)
    return POSITIONAL.sub(' ',value)


def markers(words,language):
    folded={w.casefold() for w in words}
    return folded&FUNCTION.get(language,set()),folded&UI.get(language,set())


def identifiable(words,language):
    function,ui=markers(words,language)
    # Common cross-language words never decide a label by themselves.
    return len(function)>=2 or bool(function and ui) or len(ui)>=2


def detect(source,target,source_locale,target_locale,*,escaping=False,loans=(),source_message=None,target_message=None):
    """Return (type, confidence, reason), or None for an unsupported claim."""
    sl,tl=base(source_locale),base(target_locale)
    if source_locale.lower()==target_locale.lower():return None
    # Russian source copies have already passed the lexicon-aware checker.
    # Its known shared-name/loan exceptions must not be relabelled foreign.
    if source==target and sl=='ru':return None
    visible=prose(target,escaping,target_message);words=WORDS.findall(visible)
    if not words:return None
    allowed=SHARED|{w.casefold() for w in loans}
    source_visible=prose(source,escaping,source_message)
    source_words=WORDS.findall(source_visible)
    known=set().union(*FUNCTION.values(),*UI.values())
    # Names occurring in both strings are protected only when they do not
    # also carry a language signal; "SAVE YOUR PROFILE" is still prose.
    names={w.casefold() for w in source_words if w[:1].isupper() and w.casefold() not in known}
    expected_source=script(source_locale)
    if expected_source and any(expected_source in unicodedata.name(c,'') for c in source_visible):
        # Foreign product terminology already embedded in source prose is
        # contextual vocabulary, including lowercase technical labels.
        names.update(w.casefold() for w in source_words if not any(expected_source in unicodedata.name(c,'') for c in w))
    meaningful=[w for w in words if w.casefold() not in allowed]
    if not meaningful:return None
    all_names=all(w.casefold() in names for w in meaningful)
    signals={lang for lang in FUNCTION if identifiable(words,lang)}
    if all_names and not signals:return None
    if all(w.isupper() for w in meaningful) and len(meaningful)<=4 and not signals:return None
    if source==target and sl!=tl and sl!='ru':
        # The source locale is evidence of language, not a model prediction.
        # Unknown multi-word prose is supported too; proper names abstain.
        if signals or len(meaningful)>=3 and any(w.islower() for w in meaningful):
            return 'same_as_source',.90,'source_copy'
    expected=script(target_locale)
    if expected:
        other='CYRILLIC' if expected=='LATIN' else 'LATIN'
        foreign=[w for w in meaningful if any(other in unicodedata.name(c,'') for c in w)
                 and not any(expected in unicodedata.name(c,'') for c in w)
                 and w.casefold() not in names]
        # A whole foreign clause or recognizable foreign UI instruction is
        # actionable; isolated brands and acronyms are not script defects.
        letters=sum(map(len,meaningful))
        if tl=='uz' and expected=='LATIN' and any(len(w)>=3 and w.islower() for w in foreign):
            return 'wrong_language',.90,'script_mismatch'
        if (len(foreign)>=2 and sum(map(len,foreign))>=8 and sum(map(len,foreign))>=.7*letters
                or foreign and signals-{tl}):
            return 'wrong_language',.90,'script_mismatch'
    foreign=set()
    for candidate in signals-{tl}:
        hits=set().union(*markers(words,candidate))
        others=set().union(*(FUNCTION[k]|UI.get(k,set()) for k in FUNCTION if k!=candidate))
        if hits-others and len(hits)>=2:foreign.add(candidate)
    if len(foreign)==1 and tl not in signals:
        # For languages sharing a script, require a distinctive marker as
        # well as the aggregate vote to avoid Romance cognate false positives.
        lang=next(iter(foreign));hits=set().union(*markers(words,lang))
        others=set().union(*(FUNCTION[k]|UI.get(k,set()) for k in FUNCTION if k!=lang))
        if hits-others and len(hits)>=2:return 'wrong_language',.87,'language_markers'
    return None
