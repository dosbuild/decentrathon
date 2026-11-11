"""Evidence-bounded Uzbek Latin mark consistency, independent of source keys."""
import json
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from dalel.resources import PACKAGE_ROOT
from dalel.languages.identify import prose

PROFILE=json.loads((PACKAGE_ROOT/'assets/uz.json').read_text(encoding='utf8'))
MARKS=''.join(PROFILE['apostrophe_marks'])
WORD=re.compile(r'[A-Za-z]+(?:['+re.escape(MARKS)+r'][A-Za-z]+)+')
RUNTIME_AFFIX=re.compile(r'\{[A-Za-z_][A-Za-z0-9_]*\}(?:gacha|kacha|qacha|ga|ka|qa)\b')
LEXICAL_WORD=re.compile(r"[A-Za-z]+(?:["+re.escape(MARKS)+r"][A-Za-z]+)*")
SUFFIXES=tuple('lar ning ni ga ka qa da dan im ing i si imiz ingiz miz ngiz ng m lik li siz chi cha gacha kacha qacha di gan adi ing ish moq mas may yapti ib b il'.split())


def canonical_word(word):
    return word.casefold().translate(str.maketrans({mark:"'" for mark in MARKS}))


def mark_kind(previous):return 'letter' if previous.casefold() in ('o','g') else 'separator'


@lru_cache(maxsize=1)
def lexicon():
    result=defaultdict(set)
    for line in (PACKAGE_ROOT/'assets/uzbek/lexicon.tsv').read_text(encoding='utf8').splitlines():
        word,tags=line.split('\t');result[canonical_word(word)].update(tags.split(','))
    return dict(result)


@lru_cache(maxsize=1)
def glossed_words():
    return {canonical_word(line.split('\t')[0]) for line in
        (PACKAGE_ROOT/'assets/uzbek/glossed.tsv').read_text(encoding='utf8').splitlines()}


@lru_cache(maxsize=32768)
def suffix_chain(rest,depth=0):
    return not rest or depth<4 and any(rest.startswith(s) and suffix_chain(rest[len(s):],depth+1) for s in SUFFIXES)


@lru_cache(maxsize=32768)
def lexical(word,allow_names=True):
    word=canonical_word(word);entries=lexicon()
    def allowed(tags):return bool(tags) and (allow_names or any(not t.startswith(('NP-','ABBR')) for t in tags))
    if allowed(entries.get(word)):return True
    return any(allowed(entries.get(word[:i])) and suffix_chain(word[i:]) for i in range(2,len(word)))


@lru_cache(maxsize=16384)
def spelling_candidates(word):
    word=canonical_word(word)
    if len(word)<4 or lexical(word):return ()
    options=set()
    for i,char in enumerate(word):
        if char in 'xh':options.add(word[:i]+('h' if char=='x' else 'x')+word[i+1:])
        if char in 'scn':options.add(word[:i]+{'s':'sh','c':'ch','n':'ng'}[char]+word[i+1:])
        if char in 'aeiou':
            options.add(word[:i]+char+word[i:])
            if word[i:i+2]==char*2:options.add(word[:i]+word[i+1:])
        if i and word[i-1] in 'aeiou' and char!="'":options.add(word[:i]+"'"+word[i:])
        if char=="'":options.add(word[:i]+word[i+1:])
    # Sparse dictionary coverage cannot prove that an inflected original is
    # wrong. Automatic proposals require an exact, explicitly glossed lemma;
    # productive-grammar and statistically imported entries only protect text.
    return tuple(sorted(w for w in options if w in glossed_words()))


class Orthography:
    def __init__(self,values,escaping=False):
        self.escaping=escaping;self.counts=defaultdict(Counter);self.words=Counter()
        for value in values:
            for found in WORD.finditer(prose(value,escaping)):
                word=found.group();self.words[canonical_word(word)]+=1
                for i,char in enumerate(word):
                    if char in MARKS:self.counts[mark_kind(word[i-1])][char]+=1
        self.dominant={}
        for kind,counts in self.counts.items():
            ordered=counts.most_common();total=sum(counts.values())
            if (total>=PROFILE['minimum_group_occurrences'] and ordered[0][1]/total>=PROFILE['minimum_dominant_share']
                    and (len(ordered)==1 or ordered[0][1]>ordered[1][1])):
                self.dominant[kind]=ordered[0][0]

    def summary(self):
        return {'type':'uzbek_apostrophe_policy','counts':dict(self.counts),'dominant':self.dominant,
            'policy':PROFILE['policy'],'minimum_equivalent_word_occurrences':PROFILE['minimum_equivalent_word_occurrences'],
            'lexical_correctness_claim':False}

    def edits(self,source,target,message):
        source_words={canonical_word(m.group()) for m in WORD.finditer(prose(source,self.escaping))}
        changes=[];result=target
        for start,end in message.text_spans:
            segment=target[start:end]
            # URLs, emails, tags and code-like tokens are protected just as in
            # language identification; use equal-length masking for offsets.
            masked=re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+|<[^>]*>|\b\w*[\d_]\w*\b',
                          lambda m:' '*len(m.group()),segment)
            for match in WORD.finditer(masked):
                word=match.group();identity=canonical_word(word)
                if identity in source_words or word.isupper() or self.words[identity]<PROFILE['minimum_equivalent_word_occurrences']:continue
                chars=list(word)
                for i,char in enumerate(chars):
                    if char in MARKS and mark_kind(chars[i-1]) in self.dominant:
                        chars[i]=self.dominant[mark_kind(chars[i-1])]
                fixed=''.join(chars)
                if fixed!=word:changes.append((start+match.start(),start+match.end(),word,fixed))
        for start,end,old,new in reversed(changes):result=result[:start]+new+result[end:]
        return result,[(old,new) for _,_,old,new in changes]

    def lexical_edits(self,source,target,message):
        source_words={canonical_word(m.group()) for m in LEXICAL_WORD.finditer(prose(source,self.escaping))}
        changes=[];result=target
        for start,end in message.text_spans:
            segment=re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+|<[^>]*>|\b\w*[\d_]\w*\b',lambda m:' '*len(m.group()),target[start:end])
            for match in LEXICAL_WORD.finditer(segment):
                word=match.group()
                if canonical_word(word) in source_words:continue
                candidates=spelling_candidates(word)
                if len(candidates)!=1:continue
                fixed=candidates[0];letters=list(fixed)
                for i,char in enumerate(letters):
                    if char=="'":letters[i]=self.dominant.get(mark_kind(letters[i-1]),PROFILE['standard_letter_mark'] if mark_kind(letters[i-1])=='letter' else PROFILE['standard_separator_mark'])
                fixed=''.join(letters)
                if word.isupper():fixed=fixed.upper()
                elif word[:1].isupper():fixed=fixed[:1].upper()+fixed[1:]
                changes.append((start+match.start(),start+match.end(),word,fixed))
        for start,end,old,new in reversed(changes):result=result[:start]+new+result[end:]
        return result,[(old,new) for _,_,old,new in changes]
