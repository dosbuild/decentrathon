"""Conservative, locale-aware formal checks. No issued-file keys or answers."""
from __future__ import annotations

import csv
import difflib
import re
from collections import Counter, defaultdict
from pathlib import Path

from dalel.formats.arb import ArbDocument
from dalel.formats.messages import differences, parse, protected, validate_suggestion
from dalel.contracts import Finding, finding_order
from dalel.languages.kazakh import Morphology, harmonic_i_candidates, lexicon
from dalel.engine.suggestions import placeholder_name_repair

# Constructed from ordinary international technical/game vocabulary and the
# public style-guide categories, not from a list of matching corpus strings.
LOANS = set('''гараж бампер металл кактус км ок бустер кейс ивент аукцион рейтинг бонус инвентарь
баланс варн мут фура гк vip premium premiumpass abs esp gps wi-fi usb bluetooth
интернет радио такси автобус троллейбус трамвай метро вагон локомотив трактор
мотоцикл мопед скутер велосипед самолет танк прицеп кран лифт контейнер
банк банкомат кредит депозит бизнес фирма компания акция облигация
спорт футбол хоккей теннис баскетбол волейбол гольф бокс фитнес клуб
кафе бар ресторан пицца бургер кофе какао банан лимон ананас манго киви
бетон цемент асфальт гранит мәрмәр пластик резина диск шина мотор двигатель
капот салон спойлер тюнинг неон винил сабвуфер компрессор турбо бензин дизель
сервер аккаунт чат форум модератор админ меню профиль логин пароль код id
номер телефон смартфон планшет монитор компьютер камера фото видео микрофон
тариф процент комиссия лицензия паспорт диплом сертификат билет купон промокод
фракция полиция патруль офицер сержант капитан майор полковник генерал
бронза платина титан алюминий никель хром литий неон гелий атом магнит
торт кекс вафли печенье шоколад мармелад карамель йогурт кефир майонез соус салат
апельсин мандарин грейпфрут папайя авокадо кокос финик хурма инжир фисташка
бонусы бонус vip-клуб cм мм м кг г т л мл кб мб гб мин сек мс мкс нс usd eur kzt'''.split())
RU_FUNCTION = set('''и или не нет да вы ваш ваша ваше ваши в во на из за по до для от с со к ко у при
без под над между через чтобы если этот эта это эти все весь ещё уже только'''.split())
RU_COMMON = set('''помощь выход вход отмена выбрать продолжить подтвердить купить продать закрыть открыть
назад далее принять отклонить удалить отправить получить оплатить стоимость цена время уровень
сегодня завтра вчера перерыв остановка работа задание победа поражение здоровье деньги награда
семья имущество свадьба брак крыша дверь окно колеса колёса цвет название описание количество
пожалуйста предупреждение ошибка уведомление настройки пригласить список новый новая новое новые
доступно недоступно информация требования условия правила содержание'''.split())
WORD = re.compile(r'[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]+')
CYR = re.compile('[А-Яа-яЁё]')
KK = re.compile('[ӘәҒғҚқҢңӨөҰұҮүҺһІі]')
AFFIX = re.compile(r'\{[A-Za-z_]\w*\}(?:[-‑–])?(?:ларға|лерге|дарға|дерге|тарға|терге|ның|нің|дың|дің|тың|тің|нан|нен|дан|ден|тан|тен|мен|бен|пен|ға|ге|қа|ке|да|де|та|те|ны|ні|ды|ді|ты|ті)(?=$|[^А-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІі])')
LETTER_PAIRS = {'и':'і','к':'қ','г':'ғ','у':'ұү','а':'ә','н':'ң','о':'ө','х':'һ'}
NATIVE_VALID = set('''мен сен ол біз сіз олар осы сол бұл мұны оны соны оның менің сенің
өзі өзі үшін бірақ әрі тағы ғана және дейін кейін бірге одан бұдан содан қара қора қонып қорық
киім киіну қиын қиял қасиет ақиқат биік биыл жиын жиі иіс ине иық тиын тиісті
сиыр сый сыйлық су суық суға ауа ауыл уақыт қуат қуан қуаныш тау тауы жау жауын
сан сән санын санау ана анасы анасын аға ағасы ағаның жан жаны жанына хан халық'''.split())
FOREIGN_FUNCTION = set('''the and your you are is for with please from into without
de do da dos das para por você sua seu será uma um não novamente favor que como
el los las una usted su por favor que con sin il le les des une vous votre est'''.split())


def lexical_text(text):
    return re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+|\{[^{}]*\}|<[^>]*>','',text)


def contextual_language_exception(key,source,target,ref_locale):
    if not lexical_text(target).strip() or not CYR.search(source):
        return True
    words = WORD.findall(lexical_text(target))
    if not words or (len(words)==1 and words[0].casefold() in {'lvl','xp','hp','mp','fps','afk','id','ui','vip','ok','max','min','km','kg'}):
        return True
    # Uppercase event/product names need semantic review, not a script verdict.
    if ref_locale=='en' and source.isupper() and target.isupper() and len(words)<=4:
        return True
    parts = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)',key.replace('_',' '))
    language_names = {'en':'english','de':'german','fr':'french','ru':'russian','uz':'uzbek','kk':'kazakh','pt':'portuguese'}
    return any(p.casefold() in language_names.values() for p in parts)


def fragment(text: str,limit=75):
    # Literal quote delimiters in user text cannot be nested in evidence.
    candidates = [part for part in re.split('[«»]',text) if part.strip()]
    chosen=max(candidates,key=len) if candidates else text
    words=list(re.finditer(r'\S+',chosen))
    if len(chosen)<=limit or not words:return chosen
    ends=[word.end() for word in words if word.end()<=limit]
    return chosen[:ends[-1] if ends else words[0].end()].strip()


def is_russian_untranslated(text: str) -> bool:
    # Equality with a declared Russian source supplies language context. A
    # multi-token value containing untranslated prose must not depend on a
    # closed list of Russian verb endings. Known Kazakh/loan-only lists stay
    # exempt; protected code, URLs and argument names are not language clues.
    prose=lexical_text(text)
    words=[w.casefold() for w in WORD.findall(prose) if CYR.search(w)]
    if len(text.split())>=3 and words and not KK.search(prose):
        if any(w in RU_FUNCTION or w not in LOANS and w not in lexicon() for w in words):
            return True
    # A localized title/name on one line must not mask untranslated sentences
    # elsewhere. Whitespace separates words; no regex dot is used for prose.
    if len([line for line in text.splitlines() if line.strip()])>=2 and not KK.search(text):
        words=[w.casefold() for w in WORD.findall(lexical_text(text)) if CYR.search(w)]
        # Judge the whole value; one-word lines can form an ordinary Russian
        # phrase. A list made entirely of established loans/names stays quiet.
        if words and not all(w in LOANS or w in lexicon() for w in words):
            if any(w in RU_FUNCTION for w in words) or re.search('[ёЁъЪщЩэЭ]',text):
                return True
    return any(_russian_segment(part) for part in [text,*text.splitlines()] if part.strip())


def _russian_segment(text: str) -> bool:
    stripped = re.sub(r'\{[^{}]*\}|<[^>]*>','',text)
    words = [w.casefold() for w in WORD.findall(stripped)]
    cyr = [w for w in words if CYR.search(w)]
    if not cyr or KK.search(stripped):
        return False
    meaningful = [w for w in cyr if w not in LOANS]
    if not meaningful:
        return False
    if any(w in RU_COMMON for w in meaningful):
        return True
    if len(cyr)>=2 and any(w in RU_FUNCTION for w in meaningful):
        return True
    # Distinctively Russian inflection or a productive Russian verb ending.
    return any(re.search(r'(?:ться|овать|ировать|ать|ять|еть|ить|уть|ыть|ение|ения|ений|ость|ости|ский|ская|ское|ские|ого|ему|ыми|айте|яйте|уйте|ейте|ите)$',w) for w in meaningful)


def sibling_keys(key: str,keys) -> list[str]:
    stem = re.sub(r'(?:Prefix|Suffix|Part|Title|Accent|Text|Datetime|Date|Time)\d*$','',key,flags=re.I)
    if stem==key:
        stem = re.sub(r'(?:[A-Z][a-z]*|_\w+|\d+)$','',key)
    if len(stem)<4:
        return []
    return [k for k in keys if k!=key and k.startswith(stem)]


def glossary_rows(path: Path | None):
    from dalel.terminology.glossary import rows
    return rows(path)


def orthography_edits(text: str,frequencies=None,morphology=None) -> tuple[str,list[tuple[str,str]]]:
    edits = []
    # Productive polite endings: these substitutions hold independently of stem.
    patterns = [(r'([ңн])из\b',r'\1із'),(r'([ңн])ызга\b',r'\1ызға')]
    result = text
    for pattern,replacement in patterns:
        for match in list(re.finditer(pattern,result,flags=re.I))[::-1]:
            before = match.group()
            after = match.expand(replacement)
            if before.isupper():
                after = after.upper()
            edits.append((before,after))
            result = result[:match.start()]+after+result[match.end():]
    # Latin lookalikes embedded in a Cyrillic word are deterministic mixed script.
    lookalikes = str.maketrans('aAceEoOpPcCxXyY','аАсеЕоОрРсСхХуУ')
    for match in list(WORD.finditer(result))[::-1]:
        word = match.group()
        if CYR.search(word) and re.search('[aAceEoOpPcCxXyY]',word):
            fixed = word.translate(lookalikes)
            if fixed!=word:
                edits.append((word,fixed))
                result = result[:match.start()]+fixed+result[match.end():]
    if frequencies:
        morphology = morphology or Morphology(frequencies,LOANS|NATIVE_VALID,LOANS)
        for match in list(WORD.finditer(result))[::-1]:
            original = match.group()
            word = original.casefold()
            if len(word)<3 or word in LOANS or word in NATIVE_VALID or word in morphology.lexicon:
                continue
            nasal=morphology.final_n_candidates(word)
            if morphology.protected(word) and not nasal:
                continue
            loan_possessive=morphology.possessive_candidates(word)
            native_suffix=morphology.front_suffix_i_candidates(word)
            candidates = nasal | harmonic_i_candidates(word) | morphology.back_suffix_candidates(word) | loan_possessive | native_suffix
            if not loan_possessive:
                candidates |= morphology.front_final_i_candidates(word)
            if frequencies[word]>1:
                # Repeated unknown words/names remain protected. The two new
                # structurally unambiguous shapes are independent of frequency.
                repeated=set(native_suffix)
                if re.search(r'[аоұы]й[лдт]ык',word):
                    repeated |= morphology.back_suffix_candidates(word)
                if word.endswith(('ейи','айи','ойи')):
                    repeated |= loan_possessive
                candidates &= repeated
            # Repetition cannot make a malformed productive suffix grammatical.
            # Corpus frequency still guards speculative lexical substitutions.
            for i,char in enumerate(word if frequencies[word]<=1 else ''):
                for replacement in LETTER_PAIRS.get(char,''):
                    candidate = word[:i]+replacement+word[i+1:]
                    # Third-person possessive + case and second-person
                    # possessive are both grammatical; frequency cannot select
                    # the person. These are semantic, not spelling decisions.
                    if char=='н' and replacement=='ң' and i>0 and word[i-1] in 'ыі' and morphology.lexical_valid(word):
                        continue
                    if frequencies[candidate]>=1 or (candidate in morphology.lexicon or
                            i>0 and char in 'гк' and morphology.lexical_root_change(candidate,i) or
                            morphology.lexical_root_change(candidate,i,nominal=True)) and not morphology.lexical_valid(word) or (not morphology.valid(word) and (
                            morphology.supported_root_change(candidate,i)
                            or char in 'иг' and replacement in 'іғ' and morphology.supported_suffix_change(candidate,i))):
                        candidates.add(candidate)
            if len(candidates)==1:
                fixed = candidates.pop()
                fixed = fixed.upper() if original.isupper() else fixed.capitalize() if original[0].isupper() else fixed
                edits.append((original,fixed))
                result = result[:match.start()]+fixed+result[match.end():]
    return result,edits


def check(source: ArbDocument,target: ArbDocument,refs=(),glossary: Path | None=None,escaping=False,*,processed_keys=None):
    src,tgt = source.strings,target.strings
    findings,diagnostics = [],[]
    target_locale = target.locale.split('_')[0].split('-')[0]
    source_locale = source.locale.split('_')[0].split('-')[0]
    rows = glossary_rows(glossary)
    from dalel.terminology.glossary import Matcher,source_match,entry_rule
    term_matcher=Matcher(LOANS) if rows else None
    reference_values = [(ref,ref.strings) for ref in refs]
    frequencies = Counter(w.casefold() for value in tgt.values() for w in WORD.findall(lexical_text(value)))
    morphology = Morphology(frequencies,LOANS|NATIVE_VALID,LOANS) if target_locale=='kk' else None
    uzbek=None
    if target_locale=='uz' and 'cyrl' not in target.locale.casefold():
        from dalel.languages.uzbek import Orthography
        uzbek=Orthography(tgt.values(),escaping)
        diagnostics.append(uzbek.summary())
    for label,doc in [('source',source),('target',target)]:
        diagnostics.extend(dict(type='nested_duplicate',file=label,key=entry['path'][0],**entry)
                           for entry in doc.nested_duplicates)
        grouped = defaultdict(list)
        for m in doc.members:
            grouped[m.key].append(m)
        for key,members in grouped.items():
            if len(members)>1:
                diagnostics.append({'type':'duplicate','file':label,'key':key,'occurrences':len(members),
                                    'conflict':any(m.value!=members[-1].value for m in members[:-1])})
        for key in doc.strings:
            if re.fullmatch(r'[a-z][A-Za-z0-9_]*',key) is None:
                diagnostics.append({'type':'invalid_flutter_name','file':label,'key':key})
        for key,m in doc.effective.items():
            if not key.startswith('@') and not isinstance(m.value,str):
                diagnostics.append({'type':'non_string_value','file':label,'key':key})
    for key in tgt.keys()-src.keys():
        diagnostics.append({'type':'extra_key','key':key})
        if processed_keys is not None and '@' not in key:processed_keys.append(key)
    for key,s in src.items():
        t = tgt.get(key)
        if '@' in key:
            diagnostics.append({'type':'misplaced_metadata','key':key})
            continue
        if processed_keys is not None:processed_keys.append(key)
        if not s:
            if t is None:
                diagnostics.append({'type':'missing_empty_source','key':key})
            continue
        def add(kind,evidence,severity='high',confidence=.99,suggestion=None):
            if suggestion is not None:
                from dalel.engine.verification import source_case_format
                suggestion=source_case_format(s,suggestion,escaping=escaping)
            if suggestion is not None and validate_suggestion(s,suggestion,escaping=escaping):
                diagnostics.append({'type':'unsafe_suggestion','key':key,'issue_type':kind})
                suggestion = None
                for quote in re.findall('«([^«»]*)»',evidence):
                    if not any(quote in value for value in (s,t or '')):
                        evidence = evidence.replace('«'+quote+'»',quote)
            findings.append(Finding(key=key,issue_type=kind,severity=severity,source=s,target=t,
                                    evidence=evidence,suggestion=suggestion,confidence=float(confidence)))
        if t is None:
            add('untranslated',f'Перевод отсутствует: исходная строка «{fragment(s)}» не имеет целевого значения.')
            continue
        if t=='':
            siblings = [k for k in sibling_keys(key,tgt) if tgt[k]]
            diagnostics.append({'type':'empty_value','key':key,'siblings':siblings,
                                'requires_language_review':bool(siblings)})
            if not siblings:
                add('untranslated',f'Перевод пуст: смысл исходной строки «{fragment(s)}» не перенесён; связанных фрагментов нет.')
            continue
        a,b = parse(s,escaping),parse(t,escaping)
        diffs = differences(a,b)
        pa,pb = protected(s),protected(t)
        for kind in ('tags','pua'):
            if pa[kind]!=pb[kind]:
                diagnostics.append({'type':'protected_token_mismatch','key':key,'token_kind':kind,
                                    'source':dict(pa[kind]),'target':dict(pb[kind])})
        if (re.match(r'^\s*',s).group()!=re.match(r'^\s*',t).group() or re.search(r'\s*$',s).group()!=re.search(r'\s*$',t).group()) and sibling_keys(key,tgt):
            diagnostics.append({'type':'concat_whitespace','key':key})
        for error in a.errors:
            diagnostics.append({'type':'source_icu_syntax','key':key,**error})
        for label,message in [('source',a),('target',b)]:
            for warning in message.warnings:
                diagnostics.append({'type':'icu_warning','file':label,'key':key,**warning})
        for diff in diffs:
            if diff['type']=='diagnostic':
                diagnostics.append({'type':'target_icu_syntax','key':key,**{k:v for k,v in diff.items() if k!='type'}})
        named = [d for d in diffs if d['type']=='placeholder_mismatch']
        if named or pa['positional']!=pb['positional']:
            missing,extra = sorted(a.names-b.names),sorted(b.names-a.names)
            quoted = '{'+missing[0]+'}' if missing and '{'+missing[0]+'}' in s else fragment(t)
            details = []
            if missing:
                details.append('нет '+', '.join(missing))
            if extra:
                details.append('лишние '+', '.join(extra))
            if pa['positional']!=pb['positional']:
                details.append('позиционные аргументы изменены')
            if not details:
                details.append('роль или ветвь аргумента изменена')
            suffix = '; '.join(details)[:125]
            add('placeholder_mismatch',f'Плейсхолдеры не совпадают с исходником: {suffix}. Фрагмент: «{quoted}».',
                suggestion=placeholder_name_repair(s,t,escaping))
            continue
        if any(d['type']=='plural_mismatch' for d in diffs):
            add('plural_mismatch',f'Нарушена структура ICU plural: «{fragment(t)}». По правилу нужны управляющий аргумент, допустимые селекторы и ветвь other.')
            continue
        wrong_ref = None
        language_exception = any(contextual_language_exception(key,s,t,ref.locale.split('_')[0])
                                 for ref,values in reference_values if values.get(key)==t)
        if source_locale!=target_locale and not language_exception:
            for ref,values in reference_values:
                if ref.locale.split('_')[0] == target_locale:
                    continue
                value = values.get(key)
                latin = re.findall('[A-Za-z]{3,}',re.sub(r'\{[^{}]*\}|<[^>]*>','',t))
                if value==t and t!=s and latin and any(w.casefold() not in s.casefold() for w in latin) and not contextual_language_exception(key,s,t,ref.locale.split('_')[0]):
                    from dalel.languages.identify import script,detect as detect_language
                    # A contaminated sibling is not language evidence. Nor
                    # is equality between two Latin-script locales proof
                    # that a shared name or loan is foreign to the target.
                    ref_script=script(ref.locale);target_script=script(target.locale)
                    if ref_script=='CYRILLIC' and not CYR.search(lexical_text(t)):
                        continue
                    if ref_script==target_script and not detect_language(s,t,source.locale,target.locale,escaping=escaping,loans=LOANS,source_message=a,target_message=b):
                        diagnostics.append({'type':'shared_reference_text_unproven_language','key':key,'reference_locale':ref.locale})
                        continue
                    wrong_ref = ref
                    break
        if wrong_ref:
            reference = wrong_ref.path.name if wrong_ref.path else wrong_ref.locale
            add('wrong_language',f'Текст другого языка: «{fragment(t)}» совпадает с {reference}; латинские слова отсутствуют в исходнике.',confidence=.98)
            continue
        visible = lexical_text(t)
        latin_words = re.findall('[A-Za-zÀ-ÿ]+',visible)
        foreign_markers = {w.casefold() for w in latin_words} & FOREIGN_FUNCTION
        if target_locale in ('kk','ru','uk','bg') and 'latn' not in target.locale.casefold() and not CYR.search(visible) and len(latin_words)>=5 and len(foreign_markers)>=2:
            add('wrong_language',f'Вместо целевого языка дан иностранный текст «{fragment(t)}»: несколько служебных слов другого языка и латиница вместо ожидаемой кириллицы.',confidence=.92)
            continue
        if source_locale=='ru' and target_locale!='ru' and s==t and is_russian_untranslated(t):
            add('same_as_source',f'Русский текст «{fragment(t)}» оставлен без перевода: дословно совпадает с исходником и содержит русскую лексику или грамматику.',confidence=.90)
            continue
        from dalel.languages.identify import detect as detect_language
        signal=detect_language(s,t,source.locale,target.locale,escaping=escaping,loans=LOANS,source_message=a,target_message=b)
        if signal:
            kind,confidence,reason=signal
            explanation={'source_copy':'дословно совпадает с исходным предложением при другом целевом языке',
                'script_mismatch':'содержит иноязычную фразу в письменности, не соответствующей целевой локали',
                'language_markers':'сочетание служебных слов и команд указывает на другой язык'}[reason]
            add(kind,f'Текст «{fragment(t)}» не переведён на целевой язык: {explanation}.',confidence=confidence)
            continue
        from dalel.languages.scripts import mixed_alphabet
        mixed = mixed_alphabet(s, t, b, target.locale)
        if mixed:
            add('orthography', mixed['evidence'], severity='medium', confidence=.95, suggestion=mixed['suggestion'])
            diagnostics.append(dict(type='mixed_script_letters', key=key, letters=mixed['letters'], tokens=mixed['tokens']))
            continue
        if target_locale=='kk':
            match = AFFIX.search(t)
            if match:
                from dalel.languages.kazakh import purchase_affix_rephrase
                proposal = purchase_affix_rephrase(s, t)
                add('suffix_harmony',f'К значению во время выполнения жёстко присоединён аффикс: «{match.group()}». По правилу гармонии нужен текст без аффикса либо requires_code_change.',severity='medium',confidence=.94,suggestion=proposal)
                diagnostics.append({'type':'safe_affix_rephrase' if proposal else 'requires_code_change',
                                    'key':key,'reason':'runtime_affix','fragment':match.group()})
                continue
        if uzbek is not None:
            from dalel.languages.uzbek import RUNTIME_AFFIX
            match=RUNTIME_AFFIX.search(t)
            if match:
                add('orthography',f'В «{match.group()}» вариант узбекского аффикса направления жёстко задан у динамического значения; после основ на k/q требуется ассимиляция. Нужна конструкция без зависимого аффикса либо requires_code_change.',severity='medium',confidence=.94)
                diagnostics.append({'type':'requires_code_change','key':key,'reason':'uzbek_runtime_direction_affix','fragment':match.group()})
                continue
        glossary_hit = False
        for row in rows:
            term,translation = row.get(source_locale,''),row.get(target_locale,'')
            rule=entry_rule(row,target_locale)
            metadata=source.effective.get('@'+key)
            match=source_match(s,term,rule,metadata=metadata.value if metadata else {}) if term and translation else None
            allowed=[translation,*rule.get('allowed_variants',[])]
            if match and not any(term_matcher.contains(t,value,target_locale) for value in allowed):
                suggestion=translation if match.casefold()==s.casefold() else None
                add('glossary_violation',f'Термин «{fragment(match)}» в «{fragment(t)}» не соответствует глоссарию: ожидается {translation[:60]} с допустимым словоизменением.',severity='medium',confidence=.92,suggestion=suggestion)
                if suggestion is None:
                    diagnostics.append({'type':'suggestion_unavailable','key':key,'issue_type':'glossary_violation',
                        'reason':'contextual_term_rephrase','source_term':term,'approved_target':translation})
                glossary_hit = True
                break
        if glossary_hit:
            continue
        if target_locale=='kk':
            corrected,edits = orthography_edits(t,frequencies,morphology)
            if edits:
                before,after = edits[0]
                add('orthography',f'Нарушено написание «{before}»: ожидается {after}. Основание: казахская словарная форма и гармония аффиксов.',severity='medium',confidence=.88,suggestion=corrected)
        if uzbek is not None:
            corrected,edits=uzbek.edits(s,t,b)
            from dalel.formats.messages import parse as parse_message
            lexical_fixed,lexical_edits=uzbek.lexical_edits(s,corrected,parse_message(corrected,escaping))
            corrected=lexical_fixed
            if lexical_edits:
                before,after=lexical_edits[0]
                add('orthography',f'Написание «{before}» не подтверждено узбекским словарём; единственный словарно поддержанный вариант в проверяемом классе: {after}. Основание: Apertium-uzb, x/h, диграфы, удвоение гласной или tutuq.',severity='medium',confidence=.84,suggestion=corrected)
                continue
            if edits:
                before,after=edits[0]
                add('orthography',f'В «{before}» знак отличается от устойчивого большинства для этой функции в узбекском файле; единообразный вариант: {after}. Частоты и выбранные знаки: diagnostics.json, uzbek_apostrophe_policy.',severity='medium',confidence=.90,suggestion=corrected)
        if not findings or findings[-1].key != key:
            from dalel.engine.labels import inspect_label
            padding = inspect_label(s, t, source.locale, target.locale)
            if padding:
                add(padding['issue_type'], padding['evidence'], severity=padding['severity'], confidence=.86, suggestion=padding['suggestion'])
    findings.sort(key=finding_order)
    diagnostics.sort(key=lambda d:(d.get('key',''),d['type'],d.get('file','')))
    return findings,diagnostics
