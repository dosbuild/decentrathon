"""General publication and fix constraints, independent of issued answers."""
from __future__ import annotations
import copy
import re
from collections import Counter
from dalel.languages.kazakh import Morphology,lexicon,harmony
from dalel.engine.rules import WORD,LOANS,NATIVE_VALID,RU_COMMON,RU_FUNCTION,lexical_text,orthography_edits
from dalel.formats.messages import validate_suggestion,target_layout_problems

CASE_REASON=re.compile(r'регистр|заглавн|прописн|строчн|капс|верхн.{0,16}букв',re.I)
SEMANTIC_ERROR=re.compile(r'искаж\w*\s+(?:смысл|значени)|неверн\w*\s+(?:смысл|значени|перевод)|'
    r'противоположн\w*\s+(?:смысл|значени|действи)|(?:смысл|значени\w*)\s+(?:полностью\s+)?искаж\w*|'
    r'(?:смысл|значени\w*)\s+(?:полностью\s+)?(?:неверн|не\s+связан)|'
    r'неполн\w*\s+предикат|без\s+предикат|(?:опуска\w*|опущ\w*)\s+(?:объект|субъект|действи)|'
    r'ошибочн\w*\s+(?:перевод|обознач)|не\s+(?:соответству\w*\s+(?:смысл|значени)|переда[её]т\s+смысл)|'
    r'подмен\w*\s+(?:смысл|значени|действи)|друг(?:ой|ое|ую)\s+(?:смысл|значени|действи|предмет)|'
    r'означает.{0,100}(?:а не|вместо)|семантическ\w*\s+ошиб|неологизм|изобрет[её]н\w*\s+слов|'
    r'слов\w*.{0,35}не\s+существ|несуществующ\w*\s+слов|'
    r'(?:ярлык|метк|названи|заголов)\w*.{0,180}(?:перевед|замен|превращ)\w*.{0,50}(?:инструкц|команд|действи)|'
    r'глагол\w*.{0,90}существител|существител\w*.{0,90}глагол',re.I)
LINE_REFLOW=re.compile(r'(?:неверн|ошибочн|лишн)\w*.{0,35}(?:перенос|разбиени.{0,10}строк)|перенос\w*.{0,25}(?:искаж|ошиб)',re.I)
MEANING_LOSS=re.compile(r'противополож|инверси|утрач\w*\s+смысл|потер\w*\s+смысл|неверн\w*\s+действи|'
    r'(?:смысл|значени\w*)\s+(?:полностью\s+)?искаж\w*|'
    r'друг(?:ой|ое|ую)\s+(?:действи|предмет)|не\s+переда[её]т\s+смысл|искаж\w*\s+смысл|'
    r'сломан\w*\s+(?:механик|действи)|отрицани',re.I)

def internal_case_corruption(text):
    return any(re.search('[А-ЯЁӘҒҚҢӨҰҮҺІ]',word[1:]) and not word.isupper()
               for word in WORD.findall(text or '') if re.search('[А-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІі]',word))

def case_only(record):
    target=record.get('target');suggestion=record.get('suggestion')
    if record['issue_type']!='orthography' or internal_case_corruption(target):
        return False
    same=isinstance(target,str) and isinstance(suggestion,str) and target!=suggestion and target.casefold()==suggestion.casefold()
    if same:
        return True
    # A genuine letter replacement can share a card with a case remark. The
    # evidence must actually claim that defect; an arbitrary rewrite does not
    # rescue a case-only finding.
    if re.search(r'опечат|орфограф|букв\w*.{0,30}(?:вместо|замен)|смешени\w*\s+алфавит',record.get('evidence',''),re.I):
        return False
    return bool(CASE_REASON.search(record.get('evidence','')))

CARDINALS='бір екі үш төрт бес алты жеті сегіз тоғыз он жиырма отыз қырық елу алпыс жетпіс сексен тоқсан жүз мың миллион миллиард'.split()
QUANTITY_NAME=re.compile(r'(?:^\{[nk]\}$|count|amount|quantity|number|total|num|price|sum|points|coins|remaining)',re.I)
NUMERIC_TOKEN=r'(?:\d+(?:[.,]\d+)?|\{[A-Za-z_]\w*\}|%[sd])'
ABLATIVE=r'(?:нан|нен|дан|ден|тан|тен)'
COMPARATIVE=r'(?:артық|көп|кем|аз|жоғары|төмен|көбірек|азырақ)'
APPENDED_VALUE=re.compile(r'\b(?:для|до|после|за|через|к|ко|по|от)\s*[:…-]?\s*$',re.I)


def appended_value_fragment(source):
    """A final source preposition leaves its runtime complement outside ARB."""
    return bool(APPENDED_VALUE.search(source or ''))


def shared_short_term(text):
    words=[w.casefold() for w in WORD.findall(lexical_text(text or ''))]
    if not words:return False
    if all(w in LOANS|NATIVE_VALID for w in words):return True
    return len(words)<=2 and all(w not in RU_COMMON|RU_FUNCTION and
        (w in LOANS|NATIVE_VALID or any(tag.startswith('N') for tag in lexicon().get(w,()))) for w in words)

class FixValidator:
    def __init__(self,target_values=(),*,context=None,approved_glossary=None):
        self.context=context
        self.frequencies=context.frequencies if context is not None else Counter(w.casefold() for value in target_values if isinstance(value,str)
                                 for w in WORD.findall(lexical_text(value)))
        self.morphology=Morphology(self.frequencies,LOANS|NATIVE_VALID,LOANS)
        from dalel.terminology.glossary import Matcher
        self.glossary_matcher=Matcher(LOANS)
        self.approved_words={w.casefold() for text in (context.glossary.values() if context else ()) for w in WORD.findall(text)}
        from dalel.terminology.glossary import ui_conventions
        self.approved_glossary=approved_glossary if approved_glossary is not None else [{'ru':s,'kk':t} for s,t in ui_conventions().items()]

    def original_issues(self,source,target,issues):
        from dalel.terminology.glossary import gate_findings
        rows=[]
        for issue in issues:
            item=dict(issue,source=source,target=target);evidence=issue.get('evidence','')
            if issue['issue_type']=='same_as_source' and source==target and shared_short_term(target):continue
            if issue['issue_type']=='glossary_violation':
                russian=bool(re.search(r'русск\w*\s+(?:слов|текст|основ|коре?н|стем)|русск\w*\s*«|«[^»]+»\s+с\s+русск',evidence,re.I))
                quoted=[w.casefold() for quote in re.findall('«([^«»]+)»',evidence) if quote in (target or '') for w in WORD.findall(quote)]
                unaccepted=any(w not in LOANS|NATIVE_VALID and not self.morphology.lexical_valid(w) for w in quoted)
                if SEMANTIC_ERROR.search(evidence) or russian and unaccepted:
                    item['issue_type']='same_as_source' if source==target and russian else 'machine_translation_suspect'
                    item['severity']='high' if russian or MEANING_LOSS.search(evidence) else 'medium'
            rows.append(item)
        matches=[r.get('metadata') or {} for r in self.context.rows.values()
                 if r.get('source')==source and r.get('target')==target] if self.context else []
        context=matches[0] if matches and all(v==matches[0] for v in matches) else {}
        kept,_=gate_findings(rows,self.approved_glossary,'ru','kk',metadata={None:context})
        return [{k:v for k,v in r.items() if k not in ('source','target')} for r in kept]

    def attested(self,word):
        return bool(self.frequencies[word] or word in NATIVE_VALID or self.morphology.lexical_valid(word)
                    or any(self.glossary_matcher.token_matches(word,w,'kk') for w in self.approved_words if w[:2]==word[:2])
                    or self.verb_form(word))

    def verb_form(self,word):
        # Converbs, conditionals and finite personal forms are not nominal
        # suffix chains. Require a canonical verb (including productive voice
        # or negation), then a complete compatible verbal ending.
        for split in range(2,len(word)):
            stem,ending=word[:split],word[split:]
            if stem.endswith('и') and not self.morphology.verb_stem(stem):
                canonical=[stem[:-1]+v for v in ('ы','і') if self.morphology.verb_stem(stem[:-1]+v)]
                if len(canonical)==1:
                    stem=canonical[0]
            if not self.morphology.verb_stem(stem):
                continue
            front=harmony(stem)=='front'
            a,high=('е','і') if front else ('а','ы')
            personal=('', 'м', 'ң', 'ңыз' if not front else 'ңіз', 'қ' if not front else 'к')
            forms={a,'й','п',high+'п','с'+a,'р',a+'р'}
            forms.update('с'+a+p for p in personal)
            for onset in (a,'й'):
                forms.update(onset+p for p in (('ді','мін','сің','міз','сіз') if front else ('ды','мын','сың','мыз','сыз')))
            for onset in (('ді','ті') if front else ('ды','ты')):
                forms.update(onset+p for p in personal)
            if ending in forms:
                return True
            for participle in (('ген','кен','етін','йтін','ер') if front else ('ған','қан','атын','йтын','ар')):
                if ending.startswith(participle) and self.morphology.suffix_chain(stem+participle,ending[len(participle):]):
                    return True
        return False

    def check(self,source,before,after,collective=False,*,evidence='',key=None):
        if not isinstance(after,str) or after.startswith('requires_code_change'):
            return []
        errors=[{'rule':'target_layout','detail':error} for error in
                target_layout_problems(before,after,allow_line_reflow=bool(LINE_REFLOW.search(evidence)),source=source)]
        old_text=lexical_text(before or '');new_text=lexical_text(after)
        src_text=lexical_text(source or '')
        if after!=before and after==source:
            errors.append({'rule':'source_identical_fix'})
        if after!=before and appended_value_fragment(source):
            errors.append({'rule':'appended_runtime_value_requires_code_change'})
        case_basis=src_text or old_text
        if case_basis.isupper() and sum(c.isalpha() for c in case_basis)>=2 and not new_text.isupper():
            errors.append({'rule':'source_caps_lost' if src_text else 'original_caps_lost'})
        if src_text and len(WORD.findall(src_text))<=4 and len(src_text)<=32 and len(new_text)>max(1.8*len(src_text),len(src_text)+12):
            errors.append({'rule':'short_label_expansion_needs_review','source_length':len(src_text),'fixed_length':len(new_text)})
        if self.context is not None:
            errors.extend(self.context.fix_errors(source,before,after,key=key))
        # A digit/quantity ordinal separator is not a hard harmony affix.
        # Case endings (including comparisons) are handled separately.
        ordinal=r'(?<!\w)('+NUMERIC_TOKEN+r')[-‑–](?=[А-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІі])'
        for match in re.finditer(ordinal,before or ''):
            token=match[1];tail=(before or '')[match.end():]
            if re.match(r'(?:'+ABLATIVE+r'|ға|ге|қа|ке|да|де|та|те|ның|нің|дың|дің|тың|тің|ны|ні|ды|ді|ты|ті)(?!\w)',tail,re.I):continue
            if token in after and not re.search(re.escape(token)+r'[-‑–]',after):
                errors.append({'rule':'ordinal_separator_lost','token':token})
        # Ordinal evidence can also come from the source, even if the original
        # target had already lost the separator. Names do not encode type.
        source_ordinal=r'('+NUMERIC_TOKEN+r')[-‑–](?:ый|ий|ой|ым|им|ом|ем|ая|яя|ое|ее|ю|е|й)(?!\w)'
        for match in re.finditer(source_ordinal,source or '',re.I):
            token=match[1]
            if re.search(re.escape(token)+r'\s+[А-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІі]',after):
                errors.append({'rule':'ordinal_separator_lost','token':token})
        # A bare runtime value cannot supply the case selected by these
        # postpositions. A case-bearing carrier noun or a rephrased sentence
        # is safe; selecting a fixed suffix for an unknown value is not.
        governed=r'(\{[A-Za-z_]\w*\}|%[sd])(?:</[A-Za-z][^>]*>)*\s+(кейін|соң|дейін|шейін|қарсы|қарай|таман)(?!\w)'
        for match in re.finditer(governed,after,re.I):
            errors.append({'rule':'runtime_postposition_case','token':match[1],
                'postposition':match[2],'required_case':'ablative' if match[2].casefold() in ('кейін','соң') else 'dative'})
        comparison=r'(?<!\w)('+NUMERIC_TOKEN+r')[-‑–]?'+ABLATIVE+r'\s+'+COMPARATIVE+r'(?!\w)'
        for match in re.finditer(comparison,before or '',re.I):
            token=match[1]
            preserved=re.escape(token)+r'(?:[-‑–]?'+ABLATIVE+r'|\s+[^\W\d_]+'+ABLATIVE+r')\s+'+COMPARATIVE+r'(?!\w)'
            if not re.search(preserved,after,re.I):
                errors.append({'rule':'comparison_case_lost','token':token})
        # A dangling source preposition introduces an appended duration. It
        # cannot turn the preceding counted reward/action into that duration.
        # For example, "another N attempts in [time]" is not "after N attempts".
        trailing=re.search(r'(?<!\w)(\d+)\s+[^\W\d_]+\s+через\s*$',source or '',re.I)
        if trailing and re.search(r'(?<!\w)'+re.escape(trailing[1])+r'\s+[^\W\d_]+(?:дан|ден|тан|тен|нан|нен)\s+кейін\b',after,re.I):
            errors.append({'rule':'temporal_attachment','detail':'counted object became the appended duration'})
        old={w.casefold() for w in WORD.findall(lexical_text(before or ''))}
        source_words=set(WORD.findall(lexical_text(source or '')))
        for original in dict.fromkeys(WORD.findall(lexical_text(after))):
            word=original.casefold()
            if word in old or self.attested(word):
                continue
            # A brand/code has actual source evidence. Capitalizing an arbitrary
            # Russian noun does not make it a permitted brand.
            latin=bool(re.fullmatch('[A-Za-z]+',original))
            acronym=original.isupper() and (len(original)<=3 or not re.search('[АЕЁИОУЫЭЮЯ]',original))
            if original in source_words and (latin or acronym):
                continue
            rule='excluded_corpus_attestation' if self.context is not None and self.context.excluded_tokens[word] and not self.frequencies[word] else 'unattested_new_token'
            errors.append({'rule':rule,'token':original})
        if collective:
            return errors
        collective_counts={str(value) for word,value in {'двое':2,'трое':3,'четверо':4,'пятеро':5,
            'шестеро':6,'семеро':7,'восьмеро':8,'девятеро':9,'десятеро':10,'оба':2,'обе':2}.items()
            if re.search(r'\b'+word+r'\b',source or '',re.I)}
        plain=re.sub(r'<[^>]*>','',after)
        pattern=r'(?<!\w)(?P<count>\d+(?:[.,]\d+)?|\{[A-Za-z_]\w*\}|%[sd]|'+ '|'.join(CARDINALS)+r')\s+(?P<phrase>[А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]+(?:\s+[А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]+){0,2})'
        contexts=[('plural_after_count',plain)]
        if len(WORD.findall(source or ''))<=4 and len(WORD.findall(plain))<=4:
            for prior in re.finditer(pattern,re.sub(r'<[^>]*>','',before or ''),re.I):
                if not re.search(re.escape(prior['count']),plain,re.I):
                    contexts.append(('inherited_count_plural',prior['count']+' '+plain))
        for rule,text in contexts:
          for match in re.finditer(pattern,text,re.I):
            count=match['count']
            if count in collective_counts:
                continue
            if count.startswith('{') and not QUANTITY_NAME.search(count):
                continue
            for token in match['phrase'].split():
                word=token.casefold()
                for plural in ('лар','лер','дар','дер','тар','тер'):
                    for location in range(2,len(word)-len(plural)+1):
                        if word[location:location+len(plural)]!=plural:
                            continue
                        stem=word[:location];tail=word[location+len(plural):]
                        if self.attested(stem) and self.morphology.suffix_chain(stem+plural,tail):
                            errors.append({'rule':rule,'token':token,'count':count})
                            break
                # Only adjectives/cardinals can intervene; don't reach across
                # a completed counted noun into the rest of the sentence.
                if word not in CARDINALS and not any(tag.startswith(('A','DET','NUM')) for tag in lexicon().get(word,())):
                    break
        return list({(e['rule'],e.get('token',e.get('detail',''))):e for e in errors}.values())

def clean_findings(records,validator):
    cleaned=[];diagnostics=[];rejections=[];audit=[]
    for original in records:
        record=copy.deepcopy(original)
        semantic=bool(SEMANTIC_ERROR.search(record.get('evidence','')))
        if validator.context is not None:
            reason=validator.context.route(record,semantic)
            if reason:
                diagnostics.append(dict(reason,finding=record,publication='diagnostic_only'))
                continue
        kind=record['issue_type'];source=record.get('source');target=record.get('target')
        evidence=record.get('evidence','')
        if kind=='untranslated' and isinstance(target,str) and target:
            record['issue_type']='same_as_source' if target==source else 'machine_translation_suspect'
        elif kind=='wrong_language' and re.search(r'русск\w*\s+(?:слов|текст|основ|корн)|(?:слов|текст|основ|корн)\w*.{0,25}русск',evidence,re.I):
            record['issue_type']='same_as_source' if target==source else 'machine_translation_suspect'
        elif kind=='same_as_source' and target!=source:
            record['issue_type']='machine_translation_suspect'
        if record['issue_type']=='same_as_source' and shared_short_term(target):
            diagnostics.append({'type':'shared_loan_or_short_term','finding':record,'publication':'diagnostic_only'})
            continue
        if record['issue_type']!=kind:
            audit.append({'key':record['key'],'action':'type_priority','before':kind,'after':record['issue_type']})
        if re.search(r'(?:нет|отсутству\w*|не\s+(?:входит|существу\w*)).{0,35}(?:букв|алфавит)|букв\w*.{0,45}(?:нет|отсутству\w*)',evidence,re.I) and re.search(r'казахск\w*\s+алфавит',evidence,re.I):
            letters=re.findall(r'«([А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі])»',evidence)
            if letters:
                diagnostics.append({'type':'false_alphabet_claim','finding':record,'publication':'diagnostic_only'});continue
        if case_only(record):
            diagnostics.append({'type':'cross_locale_case','finding':record,'publication':'diagnostic_only'})
            continue
        proof=[]
        if record['issue_type']=='orthography':
            _,proof=orthography_edits(target or '',validator.frequencies,validator.morphology)
            if proof and 'частота исправленного' in record['evidence']:
                before_word,after_word=proof[0]
                record['evidence']=f'Нарушено написание «{before_word}»: ожидается {after_word}. Основание: казахская словарная форма и гармония аффиксов.'
        if record['issue_type']=='orthography' and not proof and re.search(r'латиниц|смешан\w*\s+скрипт|смешени\w*\s+(?:скрипт|алфавит)',record['evidence'],re.I):
            mixed=any(re.search('[A-Za-z]',word) and re.search('[А-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІі]',word)
                      for word in WORD.findall(record.get('target') or ''))
            if not mixed:
                diagnostics.append({'type':'whole_word_script','finding':record,'publication':'diagnostic_only'})
                continue
        kind=record['issue_type'];evidence=record['evidence']
        if kind in ('orthography','length_overflow','inconsistent_term','glossary_violation') and semantic:
            record['issue_type']='machine_translation_suspect'
            if record['severity']=='low':
                record['severity']='medium'
            audit.append({'key':record['key'],'action':'retyped','before':kind,'after':record['issue_type']})
        if record['severity']=='high' and record['issue_type'] in ('orthography','suffix_harmony','glossary_violation','inconsistent_term','length_overflow'):
            record['severity']='low' if record['issue_type']=='length_overflow' else 'medium'
        if record['severity']=='high' and record['issue_type']=='machine_translation_suspect' and not MEANING_LOSS.search(evidence):
            record['severity']='medium'
        if original['severity']!=record['severity']:
            audit.append({'key':record['key'],'action':'severity_rechecked','before':original['severity'],'after':record['severity']})
        failures=validator.check(record.get('source'),record.get('target'),record.get('suggestion'),evidence=evidence,key=record['key'])
        if isinstance(record.get('suggestion'),str):
            failures.extend({'rule':'protected_structure','detail':error} for error in
                validate_suggestion(record.get('source') or '',record['suggestion'],original_target=record.get('target'),
                                    allow_line_reflow=bool(LINE_REFLOW.search(evidence))) if not error.startswith('original target '))
        if failures:
            rejections.append({'key':record['key'],'issue_type':record['issue_type'],
                               'rejected_suggestion':record['suggestion'],'evidence':record['evidence'],'errors':failures})
            record['suggestion']=None
            # Evidence referring to the rejected text must remain literal under
            # the case schema; keep the claim and unquote only that gloss.
            for quote in re.findall('«([^«»]*)»',record['evidence']):
                if not any(quote in text for text in (record.get('source') or '',record.get('target') or '')):
                    record['evidence']=record['evidence'].replace('«'+quote+'»',quote)
        cleaned.append(record)
    unique={}
    for record in cleaned:
        identity=(record['key'],record['issue_type'])
        if identity in unique:
            previous=unique[identity]
            # A reclassification can merge two descriptions of one defect.
            # Keep both raw records in diagnostics, one public card per type.
            stronger=max((previous,record),key=lambda r:({'low':0,'medium':1,'high':2}[r['severity']],r['confidence']))
            unique[identity]=stronger
            diagnostics.append({'type':'redundant_finding','finding':record if stronger is previous else previous,
                'publication':'diagnostic_only','retained_type':record['issue_type']})
        else:unique[identity]=record
    return list(unique.values()),diagnostics,rejections,audit
