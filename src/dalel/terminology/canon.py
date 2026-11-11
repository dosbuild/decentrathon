"""Measure corpus terminology using explicit validity evidence, not word counts.

An observed word is not a valid translation merely because it is in a lexicon.
Only supplied valid message identities vote; unknown alignments stay visible.
This audit does not approve glossary rows or authorize a text replacement.
"""
from collections import Counter
from functools import lru_cache
import hashlib
import re
from dalel.terminology.glossary import source_match,approved
from dalel.engine.rules import LOANS,WORD
from dalel.languages.kazakh import Morphology,compatible,lexicon,VOWELS,VOICELESS

INFLECTIONS=tuple('лар лер дар дер тар тер ның нің дың дің тың тің ға ге қа ке на не да де та те нан нен дан ден тан тен ны ні ды ді ты ті н нда нде мен бен пен ым ім ың ің ы і м ң сы сі ымыз іміз ыңыз іңіз мыз міз ңыз ңіз'.split())


@lru_cache(maxsize=16384)
def inflection(stem,tail,depth=0):
    if not tail:return True
    if depth>=4:return False
    instrumental='пен' if stem[-1] in VOICELESS else 'бен' if stem[-1] in 'жз' else 'мен'
    return any(tail.startswith(suffix) and (suffix==instrumental or suffix not in ('мен','бен','пен') and compatible(stem,suffix)) and
        inflection(stem+suffix,tail[len(suffix):],depth+1) for suffix in INFLECTIONS)


@lru_cache(maxsize=16384)
def noun_roots(word):
    roots=set()
    for end in range(3,len(word)+1):
        surface=word[:end];tail=word[end:];options={surface}
        if tail[:1] in VOWELS and surface[-1:] in ('ғ','г','б'):
            options.add(surface[:-1]+{'ғ':'қ','г':'к','б':'п'}[surface[-1]])
        if inflection(surface,tail):
            roots.update(root for root in options if root in LOANS or
                any(tag.startswith('N') and not tag.startswith('NP-') for tag in lexicon().get(root,())))
    return frozenset(roots)


def word_form(actual,base,locale):
    actual=actual.casefold();base=base.casefold()
    if actual==base:return True
    if locale.split('_')[0].split('-')[0]!='kk':return False
    variants={base}
    if base[-1:] in ('п','қ','к'):variants.add(base[:-1]+{'п':'б','қ':'ғ','к':'г'}[base[-1]])
    return bool(any(actual.startswith(stem) and (stem==base or actual[len(stem):len(stem)+1] in VOWELS)
        and inflection(stem,actual[len(stem):]) for stem in variants) or noun_roots(actual)&noun_roots(base))


def equivalent(a,b,locale):
    aa=WORD.findall(a);bb=WORD.findall(b)
    return len(aa)==len(bb) and all(word_form(x,y,locale) or word_form(y,x,locale) for x,y in zip(aa,bb))


def normalized(text):
    return ' '.join(WORD.findall(text or '')).casefold()


def audit(source,target,terms,*,valid_keys=()):
    valid_keys=set(valid_keys);results=[];morphology=Morphology(Counter(),LOANS,LOANS)
    source_locale=source.locale;target_locale=target.locale
    for term in terms:
        original=term.get(source_locale);preferred=term.get(target_locale);rule=term.get('_rule',{})
        if not original or not preferred:continue
        aliases=[original,*rule.get('source_forms',[])]
        usages=[(key,value,target.strings.get(key)) for key,value in source.strings.items()
                if source_match(value,original,rule)]
        if not usages:continue
        # Whole labels provide explicit alignment. Tagged text gives another
        # alignment signal without guessing a phrase from arbitrary prose.
        forms={normalized(preferred):preferred}
        for key,src,tgt in usages:
            if not isinstance(tgt,str):continue
            if any(normalized(src)==normalized(alias) for alias in aliases):
                if 0<len(WORD.findall(tgt))<=6 and not re.search(r'[{}%\n]',tgt):
                    forms.setdefault(normalized(tgt),tgt)
            src_tags=re.findall(r'<([A-Za-z][A-Za-z0-9]*)\b[^>]*>(.*?)</\1\s*>',src,re.S)
            tgt_tags=re.findall(r'<([A-Za-z][A-Za-z0-9]*)\b[^>]*>(.*?)</\1\s*>',tgt,re.S)
            if len(src_tags)==len(tgt_tags):
                for (a,x),(b,y) in zip(src_tags,tgt_tags):
                    if a==b and any(normalized(x)==normalized(alias) for alias in aliases):
                        forms.setdefault(normalized(y),y)
        clusters=[]
        for form in forms.values():
            cluster=next((c for c in clusters if equivalent(form,c['form'],target_locale)),None)
            if cluster is None:clusters.append({'form':form,'aliases':[form]})
            else:cluster['aliases'].append(form)
        counts=Counter();votes=Counter();details=[]
        for key,src,tgt in usages:
            actual=WORD.findall(tgt or '');spans=[]
            for i,c in enumerate(clusters):
                for value in c['aliases']:
                    words=WORD.findall(value)
                    for offset in range(len(actual)-len(words)+1):
                        if words and all(word_form(a,b,target_locale) for a,b in zip(actual[offset:offset+len(words)],words)):
                            spans.append((offset,offset+len(words),i))
            occupied=set();matched=[]
            for start,end,i in sorted(set(spans),key=lambda item:(-(item[1]-item[0]),item[0],item[2])):
                positions=set(range(start,end))
                if not occupied&positions:occupied|=positions;matched.append(i)
            # Two terms in one string may mean a legitimate comparison, not
            # an exclusive rendering. Do not use it to elect a majority.
            for i in matched:counts[i]+=1
            language_valid=True
            if target_locale.split('_')[0].split('-')[0]=='kk':
                allowed={w.casefold() for w in WORD.findall(preferred)}|LOANS
                # A negative model judgment alone cannot validate an unknown
                # Russian stem or a proper-name lexicon entry used as a noun.
                for i in matched:
                    for word in WORD.findall(clusters[i]['form']):
                        w=word.casefold();tags=lexicon().get(w,())
                        if w not in allowed and (not morphology.lexical_valid(w) or tags and all(t.startswith('NP-') for t in tags)):
                            language_valid=False
            if key in valid_keys and language_valid and len(matched)==1:votes[matched[0]]+=1
            details.append({'key':key,'source':src,'target':tgt,'matched_variants':matched,
                'validity_evidence':key in valid_keys,'language_form_supported':language_valid})
        # A loanword lexicon licenses spelling, not a preference over an
        # independently valid native majority. Only explicit policy binds.
        binding=approved(term,source_locale,target_locale)
        canonical=0;basis='unresolved_no_valid_majority'
        if binding:basis='approved_glossary_or_binding_style'
        elif votes:
            maximum=max(votes.values());winners=[i for i,count in votes.items() if count==maximum]
            if len(winners)==1:canonical=winners[0];basis='majority_of_attested_valid_usages'
            elif 0 in winners:basis='verified_glossary_breaks_valid_majority_tie'
            else:canonical=None;basis='unresolved_valid_majority_tie'
        elif rule.get('verification') and all(v.get('verdict')=='yes' for v in rule['verification']):
            basis='independently_assessed_glossary_no_valid_corpus_votes'
        else:canonical=None
        variants=[dict(c,occurrences=counts[i],valid_votes=votes[i]) for i,c in enumerate(clusters)]
        actual=[i for i,count in counts.items() if count]
        results.append({'id':rule.get('id') or hashlib.sha256((original+'\0'+preferred).encode()).hexdigest(),
            'source_term':original,'source_forms':aliases,'glossary_target':preferred,
            'canonical':clusters[canonical]['form'] if canonical is not None else None,
            'selection_basis':basis,'reviewed_by_human':False,'scope':rule.get('scope','supplied corpus'),
            'variants':variants,'usages':details,'multiple_renderings':len(actual)>1,
            'unmatched_usages':sum(not d['matched_variants'] for d in details),
            'noncanonical_usages':sum(canonical not in d['matched_variants'] for d in details) if canonical is not None else None})
    return {'terms':results,'summary':{'terms_evaluated':len(results),
        'terms_with_multiple_renderings':sum(r['multiple_renderings'] for r in results),
        'unmatched_usages':sum(r['unmatched_usages'] for r in results),
        'terms_without_selected_canon':sum(r['canonical'] is None for r in results),
        'glossary_choices_changed':sum(r['canonical'] is not None and normalized(r['canonical'])!=normalized(r['glossary_target']) for r in results),
        'human_approvals':0}}
