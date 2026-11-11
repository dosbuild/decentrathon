"""Corpus-backed term, fragment and register policy; no issued answers."""
from __future__ import annotations
import re
from collections import Counter,defaultdict
from dalel.languages.kazakh import Morphology,lexicon
from dalel.engine.rules import WORD,LOANS,NATIVE_VALID,lexical_text,sibling_keys

TAG_TEXT=re.compile(r'<([A-Za-z][A-Za-z0-9]*)\b[^>]*>(.*?)</\1\s*>',re.S)
REGISTER_REASON=re.compile(r'вежлив|неформальн|формальн\w*\s+обращ|регистр\w*\s+обращ|обращени\w*.{0,30}(?:ты|вы)|фамильяр|уважительн',re.I)
CASE_REASON=re.compile(r'регистр|заглавн|прописн|строчн|капс',re.I)
ENTITY_DESCRIPTION=re.compile(r'(?:название|имя)\s+(?:игров\w*\s+)?(?:предмет|валют|функц|раздел|экран|вкладк)|(?:item|currency|feature|screen|tab)\s+(?:name|title|label)',re.I)
PREPOSITIONS=frozenset('в во на из за по до для от с со к ко у при без под над между через о об'.split())
SOURCE_INFORMAL=re.compile(r'\b(?:ты|тебя|тебе|тобой|твой|твоя|твоё|твои)\b',re.I)
SINGULAR_REASON=re.compile(r'единственн|\bед\s*\.?\s*ч\b',re.I)
PLURAL_REASON=re.compile(r'множественн|\bмн\s*\.?\s*ч\b',re.I)

def normalized(text):
    return ' '.join(WORD.findall(text or '')).casefold()

def family(key):
    parts=re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+',key.replace('_',' '))
    return tuple(p.casefold() for p in parts[:2])

def register_markers(text):
    words={w.casefold() for w in WORD.findall(text or '')};found=set()
    if words&{'сен','сенің','сені','саған'} or any(w.endswith(('сың','сің','сыңдар','сіңдер')) for w in words):
        found.add('informal')
    if words&{'сіз','сіздің','сізді','сізге'} or any(w.endswith(('ыңыз','іңіз','ңыздар','ңіздер')) for w in words):
        found.add('polite')
    return found

class CorpusPolicy:
    def __init__(self,rows,findings=(),glossary=None,*,canon_terms=()):
        self.rows={r['key']:r for r in rows}
        self.glossary={normalized(k):v for k,v in (glossary or {}).items()}
        self.canon_terms=[r for r in canon_terms if r.get('_rule',{}).get('canon_verification',{}).get('verdict')=='yes']
        excluded={r['key'] for r in findings if r['issue_type'] in ('same_as_source','untranslated','wrong_language')}
        invalid_concepts={r['key'] for r in findings if r['issue_type'] in ('machine_translation_suspect','glossary_violation')}
        self.frequencies=Counter();self.excluded_tokens=Counter();self.term_forms=defaultdict(Counter)
        self.native_terms=defaultdict(Counter);self.family_register=defaultdict(Counter)
        self.morphology=Morphology(Counter(),LOANS|NATIVE_VALID,LOANS)
        self.eligible=[];self.excluded_keys=[]
        for key,row in self.rows.items():
            source,target=row.get('source'),row.get('target')
            tokens=[w.casefold() for w in WORD.findall(lexical_text(target or ''))]
            if not isinstance(target,str) or not target or key in excluded or target==source:
                self.excluded_tokens.update(tokens);self.excluded_keys.append(key);continue
            self.eligible.append(key);self.frequencies.update(tokens)
            src_tags=list(TAG_TEXT.finditer(source or ''));tgt_tags=list(TAG_TEXT.finditer(target))
            for original,translated in zip(src_tags,tgt_tags):
                if key not in invalid_concepts and original[1]==translated[1]:
                    self.term_forms[normalized(original[2])][translated[2]]+=1
            src_term=normalized(source)
            if len(WORD.findall(source or ''))<=4 and src_term:
                # A fluent native word with the wrong meaning does not attest
                # a translation of this source concept. Its individual words
                # can still attest vocabulary elsewhere.
                if key not in invalid_concepts and self.native(target):
                    self.native_terms[src_term][target]+=1
                # Standalone labels also supply candidate forms for a source
                # term explicitly referenced inside a game tag elsewhere.
                if key not in invalid_concepts and len(tokens)<=4:
                    self.term_forms[src_term][target]+=1
            registers=register_markers(target)
            if len(registers)==1:
                self.family_register[family(key)].update(registers)
        self.tagged_source_terms={normalized(match[2]) for row in self.rows.values()
                                  for match in TAG_TEXT.finditer(row.get('source') or '')}

    def native(self,text):
        words=[w.casefold() for w in WORD.findall(lexical_text(text or ''))]
        return bool(words and len(words)<=4 and all(w not in LOANS and
                    (w in NATIVE_VALID or self.morphology.lexical_valid(w)) for w in words))

    def game_term(self,record):
        source=record.get('source') or '';term=normalized(source)
        row=self.rows.get(record['key'],{});metadata=row.get('metadata') or {}
        described=(isinstance(metadata,dict) and (metadata.get('x-locqa-term') is True
                    or isinstance(metadata.get('description'),str) and ENTITY_DESCRIPTION.search(metadata['description'])))
        if term in self.glossary or term in self.tagged_source_terms or described or self.canon_scope(source):
            return True
        old_tags=list(TAG_TEXT.finditer(record.get('target') or ''))
        new_tags=list(TAG_TEXT.finditer(record.get('suggestion') or ''))
        if any(a[1]==b[1] and a[2].casefold()!=b[2].casefold() for a,b in zip(old_tags,new_tags)):
            return True
        quotes=re.findall('«([^«»]+)»',record.get('evidence',''))
        return any(quote and quote in tag[2] for quote in quotes for tag in old_tags+list(TAG_TEXT.finditer(source)))

    def canon_scope(self,source):
        from dalel.terminology.glossary import source_match
        return any(source_match(source or '',r.get('ru',''),r.get('_rule')) for r in self.canon_terms if r.get('ru'))

    def canon_aligned(self,source,target,evidence=''):
        from dalel.terminology.glossary import source_match,Matcher
        scoped=[r for r in self.canon_terms if r.get('ru') and source_match(source or '',r['ru'],r.get('_rule'))]
        matcher=Matcher(LOANS)
        if not scoped or not all(matcher.contains(target or '',r['kk'],'kk') for r in scoped):return False
        # Matching one known term cannot absolve another concept in a longer
        # sentence. Require a whole-label scope or an exact quoted term.
        quotes=re.findall('«([^«»]+)»',evidence)
        for term in scoped:
            forms={normalized(s) for s in [term['ru'],*term.get('_rule',{}).get('source_forms',[])]}
            if normalized(source) in forms:return True
            if any(normalized(q) in forms or len(WORD.findall(q))==len(WORD.findall(term['kk']))
                   and matcher.contains(q,term['kk'],'kk') for q in quotes):return True
        return False

    def allowed_tag_forms(self,source_term):
        term=normalized(source_term)
        if term in self.glossary:
            return {self.glossary[term]}
        counts=self.term_forms.get(term,{})
        if not counts:
            return set()
        maximum=max(counts.values())
        winners={value for value,count in counts.items() if count==maximum}
        return winners if len(winners)==1 else set()

    def fix_errors(self,source,before,after,*,key=None):
        errors=[];old_tags=list(TAG_TEXT.finditer(before or ''));new_tags=list(TAG_TEXT.finditer(after))
        registers=register_markers(after)
        counts=self.family_register.get(family(key),Counter()) if key is not None else Counter()
        dominant=counts.most_common(1)
        explicit_informal=bool(SOURCE_INFORMAL.search(source or '') and register_markers(before)=={'informal'})
        if len(registers)>1:
            errors.append({'rule':'mixed_register'})
        elif explicit_informal and registers and 'informal' not in registers:
            errors.append({'rule':'explicit_source_register','expected':'informal'})
        elif not explicit_informal and registers and dominant and sum(counts.values())>=3 and dominant[0][1]/sum(counts.values())>=.8 and dominant[0][0] not in registers:
            errors.append({'rule':'family_register','dominant':dominant[0][0],'evidence_counts':dict(counts)})
        source_tags=list(TAG_TEXT.finditer(source or ''))
        for i,(old,new) in enumerate(zip(old_tags,new_tags)):
            if old[1]!=new[1] or old[2].casefold()==new[2].casefold():
                continue
            source_term=source_tags[i][2] if i<len(source_tags) and source_tags[i][1]==old[1] else ''
            # Restoring literalized argument braces does not rename a term.
            if re.sub(r'\{(\w+)\}',r'\1',new[2])==old[2]:
                from dalel.formats.messages import validate_suggestion
                if not validate_suggestion(source_term,new[2],original_target=old[2]):continue
            from dalel.engine.rules import AFFIX
            if AFFIX.sub(lambda m: m[0][:m[0].index('}') + 1], old[2]) == new[2]:
                continue
            allowed=self.allowed_tag_forms(source_term)
            from dalel.terminology.glossary import Matcher
            matcher=Matcher(LOANS)
            if not any(len(WORD.findall(new[2]))==len(WORD.findall(term)) and matcher.contains(new[2],term,'kk') for term in allowed):
                errors.append({'rule':'tag_term_rename','token':new[2],'original_term':old[2],
                               'allowed_forms':sorted(allowed),'source_term':source_term})
        source_words=set(normalized(source).split())
        # Established loans are part of the style policy, even when a native
        # synonym occurs elsewhere. Corpus occurrence alone cannot license an
        # otherwise unknown Russian stem, including an inherited bad token.
        for word in {w.casefold() for w in WORD.findall(lexical_text(after))}:
            if word in LOANS or word in NATIVE_VALID or self.morphology.lexical_valid(word):
                continue
            for stem in source_words:
                options=self.native_terms.get(stem,{})
                if (len(stem)>=3 and stem not in LOANS and stem not in lexicon() and options and word.startswith(stem)
                        and self.morphology.suffix_chain(stem,word[len(stem):])):
                    errors.append({'rule':'native_corpus_equivalent','token':word,'native_forms':sorted(options)})
        return errors

    def route(self,record,semantic=False):
        """Return a diagnostic reason, or None when the finding stays eligible."""
        kind=record['issue_type'];target=record.get('target');source=record.get('source') or ''
        suggestion=record.get('suggestion');evidence=record.get('evidence','')
        if kind=='inconsistent_term':
            if self.canon_aligned(source,target,evidence):return {'type':'current_canon_already_matched','severity':'low'}
            if isinstance(target,str) and isinstance(suggestion,str) and target.casefold()==suggestion.casefold():
                return {'type':'terminology_case','severity':'low'}
            if CASE_REASON.search(evidence) and not semantic:
                return {'type':'terminology_case','severity':'low'}
            if not semantic and not self.game_term(record):
                return {'type':'terminology_unification','severity':'low'}
            if isinstance(target,str) and isinstance(suggestion,str) and not semantic:
                from dalel.terminology.glossary import Matcher
                if Matcher(LOANS).contains(target,suggestion,'kk') or Matcher(LOANS).contains(suggestion,target,'kk'):
                    return {'type':'terminology_inflection','severity':'low'}
            counts=self.term_forms.get(normalized(source),{})
            if not semantic and not self.canon_scope(source) and len(counts)>1 and sum(counts.values())>=3 and target in counts and counts[target]==max(counts.values()):
                return {'type':'terminology_majority','severity':'low'}
        if kind=='machine_translation_suspect' and not semantic and SINGULAR_REASON.search(evidence) and PLURAL_REASON.search(evidence):
            counted=bool(re.search(r'\d|\{[^}]+\}|%[sd]|\b(?:один|одна|одно|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять|двое|трое)\b',source,re.I))
            collection=bool(re.search(r'вкладк|список|коллекци|перечисл',evidence,re.I))
            if not counted and not collection:
                return {'type':'kazakh_generic_singular','severity':'low'}
        if kind=='machine_translation_suspect' and isinstance(target,str):
            short=(0<len(WORD.findall(source))<=4 and 0<len(WORD.findall(target))<=4
                and len(source)<=45 and len(target)<=55 and not re.search(r'[!?{}%\d]',source+target))
            metadata=self.rows.get(record['key'],{}).get('metadata') or {}
            description=metadata.get('description','') if isinstance(metadata,dict) else ''
            instruction=bool(re.search(r'instruction|tutorial|инструкци|обучени',description,re.I))
            if short and not instruction and not SOURCE_INFORMAL.search(source):
                if not semantic and re.search(r'инфинитив|отглагольн',evidence,re.I) and re.search(r'императив|повелительн',evidence,re.I):
                    words=WORD.findall(target)
                    if any(w.casefold().endswith('у') and self.morphology.verb_stem(w.casefold()[:-1]) for w in words):
                        return {'type':'short_ui_action_noun_needs_context','severity':'low'}
                if (isinstance(suggestion,str) and re.search(r'притяжательн',evidence,re.I)
                        and not re.search(r'\b(?:мой|моя|моё|мои|твой|твоя|его|её|ее|их|наш|ваш|свой)\b',source,re.I)):
                    from dalel.terminology.glossary import Matcher
                    matcher=Matcher(LOANS);before_words=WORD.findall(target);after_words=WORD.findall(suggestion)
                    if len(before_words)==len(after_words) and all(matcher.token_matches(a,b,'kk') and
                            matcher.token_matches(b,a,'kk') for a,b in zip(before_words,after_words)):
                        return {'type':'short_ui_possessive_needs_context','severity':'low'}
        if kind=='glossary_violation' and not self.glossary and not semantic:
            return {'type':'no_approved_glossary','severity':'low'}
        if kind=='untranslated' and target=='':
            targets={k:r.get('target') for k,r in self.rows.items()}
            siblings=[k for k in sibling_keys(record['key'],targets) if targets[k]]
            if siblings or normalized(source) in PREPOSITIONS:
                return {'type':'empty_fragment','siblings':siblings,'severity':'low'}
        if REGISTER_REASON.search(evidence) and not semantic:
            markers=register_markers(target);counts=self.family_register.get(family(record['key']),Counter())
            dominant=counts.most_common(1)
            contradictory=bool(len(markers)==1 and dominant and sum(counts.values())>=3
                               and dominant[0][1]/sum(counts.values())>=.8 and dominant[0][0] not in markers)
            if len(markers)<2 and not contradictory:
                return {'type':'register_preference','severity':'low','family_register':dict(counts)}
        if kind=='wrong_language' and isinstance(target,str):
            words=[w.casefold() for w in WORD.findall(lexical_text(target))]
            if words and len(words)<=4 and all(re.fullmatch('[а-яёәғқңөұүһі]+',w) and
               (w in LOANS or w in NATIVE_VALID or self.morphology.lexical_valid(w) or self.morphology.protected(w)) for w in words):
                return {'type':'valid_kazakh_loan_or_word','severity':'low'}
        return None
