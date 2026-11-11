"""Explicit glossary policy and token-boundary Kazakh inflection matching."""
from __future__ import annotations
import csv
import hashlib
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from dalel.resources import PACKAGE_ROOT
from datetime import datetime
from dalel.languages.kazakh import Morphology,lexicon,VOWELS,compatible,VOICELESS

WORD=re.compile(r'[^\W\d_]+',re.UNICODE)

def ui_conventions():
    """Small explicit UI style dictionary; not inferred from client frequency."""
    path=PACKAGE_ROOT/'assets/kazakh/ui_terms.tsv'
    return dict(line.split('\t') for line in path.read_text(encoding='utf8').splitlines() if line)


def ui_variants():
    """General UI synonyms: correctness is broader than preferred house style."""
    path=PACKAGE_ROOT/'assets/kazakh/ui_variants.tsv';result={}
    for line in path.read_text(encoding='utf8').splitlines():
        if line:
            source,target=line.split('\t');result.setdefault(source.casefold(),[]).append(target)
    return result

def sidecar(path):
    return Path(path).with_name('glossary_rules.json') if path else None

def rules_digest(path):
    rules=sidecar(path)
    return hashlib.sha256(rules.read_bytes()).hexdigest() if rules and rules.is_file() else None


def entry_rule(entry,target_locale):
    """Keep consumer CSV scopes and language-specific alternatives executable."""
    rule=dict(entry.get('_rule',{}))
    alternatives=entry.get(target_locale.split('_')[0].split('-')[0]+'_variants','')
    if alternatives:
        rule['allowed_variants']=list(dict.fromkeys([*rule.get('allowed_variants',[]),
            *(v.strip() for v in alternatives.split('|') if v.strip())]))
    if entry.get('scope','').strip():rule['context_scope']=entry['scope'].strip()
    return rule


def scope_applies(scope,source,metadata=None):
    """A scoped contract needs its literal context, never a guessed key prefix."""
    if not scope:return True
    values=[source]
    def collect(value):
        if isinstance(value,str):values.append(value)
        elif isinstance(value,dict):
            for child in value.values():collect(child)
        elif isinstance(value,list):
            for child in value:collect(child)
    collect(metadata)
    needle=' '.join(scope.casefold().split())
    return any(needle in ' '.join(v.casefold().split()) for v in values if isinstance(v,str))

def rows(path,*,include_candidates=False):
    if path is None:return []
    path=Path(path)
    with path.open(encoding='utf-8-sig',newline='') as stream:
        records=list(csv.DictReader(stream))
    rules_path=sidecar(path)
    if not rules_path.is_file():
        return [r for r in records if include_candidates or not r.get('status','').startswith('ai_')]
    policy=json.loads(rules_path.read_text(encoding='utf8'))
    if policy.get('format')!=1 or not isinstance(policy.get('terms'),list):
        raise ValueError('Invalid glossary rules format')
    source=policy['source_locale'];target=policy['target_locale'];indexed={}
    for rule in policy['terms']:
        identity=(rule['source_term'].casefold(),rule['target_term'].casefold())
        if identity in indexed:raise ValueError('Duplicate glossary policy term')
        if not isinstance(rule.get('enforce'),bool):raise ValueError('Explicit glossary enforcement flag required')
        for field in ('allowed_variants','source_forms','source_exclusions'):
            values=rule.get(field,[])
            if not isinstance(values,list) or any(not isinstance(v,str) or not v.strip() for v in values):
                raise ValueError('Glossary '+field+' must be a list of nonempty strings')
        indexed[identity]=rule
    selected=[]
    for record in records:
        identity=(record.get(source,'').casefold(),record.get(target,'').casefold())
        if identity not in indexed:raise ValueError('Glossary CSV has no matching policy entry')
        rule=indexed[identity]
        entry=dict(record,_rule=dict(rule))
        if source=='ru' and target=='kk' and record.get(source,'').casefold() in ui_variants():
            entry['_rule']['allowed_variants']=list(dict.fromkeys([*rule.get('allowed_variants',[]),*ui_variants()[record[source].casefold()]]))
        if include_candidates or approved(entry,source,target):selected.append(entry)
    return selected


def approved(entry,source_locale,target_locale):
    """AI preference is never authority for finding a defect in existing text.

    A plain consumer-supplied CSV is an explicit glossary contract. Generated
    rows carry ai_* status; only the binding UI dictionary or a recorded named
    human approval can authorize those rows for detection.
    """
    rule=entry.get('_rule',{});status=rule.get('status',entry.get('status',''))
    if source_locale=='ru' and target_locale=='kk':
        binding={a.casefold():b.casefold() for a,b in ui_conventions().items()}
        if binding.get(entry.get(source_locale,'').casefold())==entry.get(target_locale,'').casefold():return True
    if status=='human_approved':
        approval=rule.get('approval',{})
        try:stamp=datetime.fromisoformat(approval.get('at','').replace('Z','+00:00'))
        except (TypeError,ValueError):return False
        return bool(rule.get('enforce') and approval.get('reviewer','').strip() and stamp.tzinfo and approval.get('attest_human') is True)
    if status.startswith('ai_'):return False
    return bool(rule.get('enforce',True))


def gate_findings(records,entries,source_locale,target_locale,*,metadata=None):
    """Recheck model/replay glossary claims against approved terms only."""
    matcher=Matcher();terms=[r for r in entries if approved(r,source_locale,target_locale)]
    retained=[];diagnostics=[]
    for record in records:
        if record['issue_type']!='glossary_violation':retained.append(record);continue
        supported=False
        for entry in terms:
            source=entry.get(source_locale);target=entry.get(target_locale);rule=entry_rule(entry,target_locale)
            context=(metadata or {}).get(record.get('key'),{})
            if not source or not target or not source_match(record.get('source') or '',source,rule,metadata=context):continue
            if not any(matcher.contains(record.get('target') or '',value,target_locale) for value in [target,*rule.get('allowed_variants',[])]):
                supported=True;break
        # Type precedence belongs to the final cross-detector merge. Keeping
        # an authorized term here prevents an earlier language row from
        # silently overriding the approved glossary's primary type.
        if supported:retained.append(record)
        else:diagnostics.append({'type':'unapproved_glossary_preference','finding':record,'publication':'diagnostic_only'})
    return retained,diagnostics


def policy_map(entries,source_locale,target_locale):
    """Explicit source aliases retain the same approved target in prompts/tags."""
    result={};ambiguous=set()
    for entry in entries:
        target=entry.get(target_locale)
        if not target or entry_rule(entry,target_locale).get('context_scope'):continue
        for source in [entry.get(source_locale),*entry.get('_rule',{}).get('source_forms',[])]:
            if source:
                prior=result.get(source)
                if source in ambiguous:continue
                if prior is not None and prior!=target:
                    result.pop(source,None);ambiguous.add(source);continue
                result[source]=target
    return result

class Matcher:
    def __init__(self,loans=()):
        self.loans=frozenset(loans);self.morphology=Morphology(Counter(),loans,loans)

    @lru_cache(maxsize=4096)
    def forms(self,canonical):
        word=canonical.casefold();forms={word}
        if word.endswith('у') and len(word)>2 and self.morphology.verb_stem(word[:-1]):
            forms.add(word[:-1])
        # Multiword citations commonly contain a possessive/case-marked head.
        # Compare inflection through its attested noun stem, including voicing,
        # so a glossary singular does not reject a grammatical plural form.
        for split in range(2,len(word)):
            root,tail=word[:split],word[split:]
            candidates={root}
            if tail and tail[0] in VOWELS and root[-1:] in ('б','ғ','г'):
                candidates.add(root[:-1]+{'б':'п','ғ':'қ','г':'к'}[root[-1]])
            for stem in candidates:
                if stem in self.loans or any(tag.startswith('N') for tag in lexicon().get(stem,())):
                    if self.morphology.suffix_chain(root,tail):forms.add(stem)
        return forms

    @lru_cache(maxsize=4096)
    def participle_concepts(self,word):
        """Lexical term identity across participles/past forms, not negation.

        A glossary term does not decide whether an affirmative or negative
        predicate is faithful to the source. Meaning checks retain that job.
        """
        result=set()
        for split in range(2,len(word)):
            stem,tail=word[:split],word[split:]
            if not self.morphology.verb_stem(stem):continue
            for ending in ('ған','ген','қан','кен','атын','етін','йтын','йтін','ды','ді','ты','ті'):
                if not tail.startswith(ending) or not compatible(stem,ending):continue
                if not self.morphology.suffix_chain(stem+ending,tail[len(ending):]):continue
                root=stem
                if root.endswith(('ма','ме','ба','бе','па','пе')) and self.morphology.verb_stem(root[:-2]):
                    root=root[:-2]
                result.add(root)
        return result

    @lru_cache(maxsize=65536)
    def token_matches(self,actual,canonical,locale):
        actual=actual.casefold();canonical=canonical.casefold()
        if actual==canonical:return True
        if locale.replace('-','_').split('_')[0]=='uz':
            from dalel.languages.uzbek import canonical_word
            actual=canonical_word(actual);canonical=canonical_word(canonical)
            if actual==canonical:return True
            # Nominal number/possession/case preserve a glossary concept;
            # derivational suffixes and arbitrary prefix matches do not.
            ending=r'(?:lar)?(?:im|ing|i|si|imiz|ingiz|miz|ngiz|ng|m)?(?:ning|ni|ga|ka|qa|da|dan|dagi|gacha|kacha|qacha)?'
            if actual.startswith(canonical) and re.fullmatch(ending,actual[len(canonical):]):return True
            # A compound's citation head can already carry third-person
            # possession: match other nominal forms of that same vowel stem.
            # Do not strip arbitrary final i or accept derivational suffixes.
            if len(canonical)>3 and canonical.endswith('si') and canonical[-3] in 'aeiou':
                stem=canonical[:-2]
                if actual.startswith(stem) and re.fullmatch(ending,actual[len(stem):]):return True
            if canonical.endswith(('q','k')):
                voiced=canonical[:-1]+("g'" if canonical[-1]=='q' else 'g')
                if actual.startswith(voiced) and re.fullmatch(r'i(?:m|ng|miz|ngiz)?(?:ning|ni|ga|da|dan)?',actual[len(voiced):]):return True
            return False
        if locale.replace('-','_').split('_')[0]!='kk':return False
        if self.participle_concepts(actual)&self.participle_concepts(canonical):return True
        for root in self.forms(canonical):
            variants={root}
            if root[-1:] in ('п','қ','к'):
                variants.add(root[:-1]+{'п':'б','қ':'ғ','к':'г'}[root[-1]])
            if root.endswith('ь'):variants.add(root[:-1])
            for stem in variants:
                if not actual.startswith(stem):continue
                rest=actual[len(stem):]
                if stem!=root and (not rest or rest[0] not in VOWELS):continue
                if self.term_inflection(stem,rest):return True
                if root.endswith('ь') and stem==root[:-1] and self.term_inflection('іл',rest):return True
                # Borrowed -нг stems can devoice before a т-case ending.
                # The canonical root still must match exactly; this relaxes
                # only the following inflection, never arbitrary substrings.
                if stem.endswith('нг') and rest.startswith('т') and self.morphology.suffix_chain('ік',rest):return True
                # The final я of borrowed -ия selects back-vowel endings;
                # an earlier е must not override its pronounced vowel.
                if stem.endswith('ия') and self.morphology.suffix_chain('а',rest):return True
        return False

    def term_inflection(self,stem,rest):
        if self.morphology.suffix_chain(stem,rest):return True
        # Instrumental -мен/-бен/-пен does not obey vowel harmony. Match the
        # preceding noun inflections and the actual final consonant separately.
        for ending in ('мен','бен','пен'):
            if not rest.endswith(ending):continue
            before=stem+rest[:-3]
            expected='пен' if before[-1] in VOICELESS else 'бен' if before[-1] in 'жз' else 'мен'
            if ending==expected and self.morphology.suffix_chain(stem,rest[:-3]):return True
        return False

    def contains(self,text,term,locale):
        tokenizer=WORD
        if locale.replace('-','_').split('_')[0]=='uz':
            from dalel.languages.uzbek import LEXICAL_WORD
            tokenizer=LEXICAL_WORD
        actual=tokenizer.findall(text or '');expected=tokenizer.findall(term or '')
        if not expected:return False
        return any(all(self.token_matches(a,b,locale) for a,b in zip(actual[i:i+len(expected)],expected))
                   for i in range(len(actual)-len(expected)+1))

@lru_cache(maxsize=2048)
def source_patterns(term,forms):
    return tuple(re.compile(r'(?<!\w)'+re.escape(variant).replace(r'\ ',r'\s+')+r'(?!\w)',re.I)
                 for variant in (term,*forms) if isinstance(variant,str) and variant.strip())


def source_match(source,term,rule=None,*,metadata=None):
    if not scope_applies((rule or {}).get('context_scope'),source,metadata):return None
    if any(source.casefold()==value.casefold() for value in (rule or {}).get('source_exclusions',[])):
        return None
    forms=tuple((rule or {}).get('source_forms',[]))
    if (rule or {}).get('match')=='whole_message_casefold':
        return source if any(source.casefold()==v.casefold() for v in (term,*forms) if isinstance(v,str) and v.strip()) else None
    for pattern in source_patterns(term,forms):
        match=pattern.search(source)
        if match:return match.group()
    return None
