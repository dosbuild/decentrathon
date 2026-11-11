"""Conservative corpus-backed Kazakh morphology for spelling candidates.

This is a small productive suffix recognizer, not a dictionary or a complete
morphological analyzer. Roots and compound components come from the current ARB.
An alternative is accepted only when it has one unambiguous supported spelling.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from dalel.resources import PACKAGE_ROOT
import re


def purchase_affix_rephrase(source, target):
    """Restructure an exact purchase label without guessing runtime harmony."""
    match = re.fullmatch(
        r'(?P<left>[ \t]*)(?P<token>\{(?P<name>[A-Za-z_]\w*)\})-?(?:ға|ге|қа|ке)'
        r' +сатып алу(?P<punct>[.!?]?)(?P<right>[ \t]*)', target)
    if not match:
        return None
    token = re.escape(match['token'])
    if not re.fullmatch(r'\s*(?:Купить|Приобрести)\s+за\s+' + token + r'[.!?]?\s*', source, re.I):
        return None
    return match['left'] + match['token'] + ' бағасына сатып алу' + match['punct'] + match['right']

FRONT = frozenset('әөүіе')
BACK = frozenset('аоұы')
VOWELS = FRONT | BACK | frozenset('иуэёюя')
VOICELESS = frozenset('пфкқтсшщхһцч')
# Suppletive demonstrative bases (сол/бұл/ол) before case/comparative suffixes.
PRONOMINAL_BASES = frozenset(('сон','мұн','бұн','он'))
# Abbreviated measurement symbols are not ordinary noun stems. In particular,
# their concatenation with a letter can itself be an unrelated borrowed word.
UNIT_SYMBOLS=frozenset('мм см км кг мг мкг мл кл кб мб гб мин сек мс мкс нс'.split())
# Nominal inflection and productive derivation; shared vowels are deliberately
# absent. Ambiguous и/у in roots is not itself evidence of a spelling defect.
SUFFIXES = tuple('''лар лер дар дер тар тер ның нің дың дің тың тің
ға ге қа ке на не да де та те нан нен дан ден тан тен
ны ні ды ді ты ті н нда нде мен бен пен
ым ім ың ің ы і м ң сы сі ымыз іміз ыңыз іңіз мыз міз ңыз ңіз
лық лік дық дік тық тік лы лі ды ді ты ті сыз сіз шы ші
ша ше дай дей тай тей ғыш гіш қыш кіш
ған ген қан кен ғы гі қы кі ғыз гіз қыз кіз
ма ме ба бе па пе дың дің тыңыз тіңіз у ла ле
мын мін бын бін пын пін сың сің сыз сіз мыз міз быз біз пыз піз'''.split())
NOUN_SUFFIXES=tuple('''лар лер дар дер тар тер ның нің дың дің тың тің
ға ге қа ке на не да де та те нан нен дан ден тан тен
ны ні ды ді ты ті н нда нде мен бен пен
ым ім ың ің ы і м ң сы сі ымыз іміз ыңыз іңіз мыз міз ңыз ңіз'''.split())


@lru_cache(maxsize=1)
def lexicon():
    """Public canonical words and continuation classes, with pinned provenance."""
    path=PACKAGE_ROOT/'assets/kazakh/lexicon.tsv'
    return {word:tuple(tags.split(',')) for word,tags in
            (line.split('\t') for line in path.read_text(encoding='utf8').splitlines())}


def harmony(word):
    """Last unambiguous vowel, or None for a root with only neutral vowels."""
    for char in reversed(word):
        if char in FRONT:
            return 'front'
        if char in BACK:
            return 'back'
    return None


def loan_harmony(word):
    """High suffix vowel in Russian loans (Apertium N5 phonology).

    Unlike native glides, Russian и/у carry vowel quality. Final front velars
    and the soft sign also select front high vowels in possessive endings.
    """
    if word.endswith(('г','к','ь','ий')):
        return 'front'
    for char in reversed(word):
        if char in 'әөүіеиэё':
            return 'front'
        if char in 'аоұыуяю':
            return 'back'
    return None


def compatible(stem, suffix):
    side = harmony(stem)
    vowels = set(suffix)
    if side == 'front' and vowels & BACK or side == 'back' and vowels & FRONT:
        return False
    # These phonological alternations exclude accidental short-root matches.
    last = stem[-1]
    if suffix[0] in 'тқк' and last not in VOICELESS:
        return False
    if suffix[0] in 'дғгб' and last in VOICELESS:
        return False
    if suffix in ('лар','лер') and last not in VOWELS | frozenset('рй'):
        return False
    return True


class Morphology:
    def __init__(self, frequencies, excluded=(), loans=()):
        self.frequencies = frequencies
        self.excluded = frozenset(excluded) | PRONOMINAL_BASES
        self.roots = frozenset(word for word,count in frequencies.items()
                               if count >= 3 and len(word) >= 3)
        self.lexicon = lexicon()
        self.loans=frozenset(loans)

    @lru_cache(maxsize=16384)
    def suffix_chain(self, stem, rest, depth=0):
        if not rest:
            return True
        if depth >= 8:
            return False
        return any(rest.startswith(suffix) and compatible(stem,suffix)
                   and self.suffix_chain(stem+suffix,rest[len(suffix):],depth+1)
                   for suffix in SUFFIXES)

    @lru_cache(maxsize=16384)
    def analyses(self, word):
        result = []
        if word in self.roots:
            result.append((word,))
        for split in range(3,len(word)):
            root,rest = word[:split],word[split:]
            if root not in self.roots:
                continue
            if self.suffix_chain(root,rest):
                result.append((root,rest))
            # A compound needs independently attested components, not merely
            # an arbitrary embedded substring. Its final component may inflect.
            for end in range(split+3,len(word)+1):
                second = word[split:end]
                if second in self.roots and self.suffix_chain(second,word[end:]):
                    result.append((root,second,word[end:]))
        return tuple(result)

    def valid(self, word):
        return bool(self.analyses(word))

    @lru_cache(maxsize=16384)
    def lexical_valid(self, word):
        if word in self.lexicon:
            return True
        for split in range(3,len(word)):
            stem,rest=word[:split],word[split:]
            roots={stem}
            if rest[0] in VOWELS and stem[-1] in 'ғгб':
                roots.add(stem[:-1]+{'ғ':'қ','г':'к','б':'п'}[stem[-1]])
            if any(root in self.lexicon for root in roots) and self.suffix_chain(stem,rest):
                return True
            soft=stem+'ь'
            if rest[0] in VOWELS and any(tag.startswith('N5') for tag in self.lexicon.get(soft,())):
                if self.suffix_chain('іл' if loan_harmony(soft)=='front' else 'ыл',rest):return True
        return False

    @lru_cache(maxsize=16384)
    def nominal_chain(self,stem,rest,depth=0):
        if not rest:return True
        if depth>=4:return False
        return any(rest.startswith(suffix) and compatible(stem,suffix)
            and (suffix not in ('н','нда','нде','нан','нен','на','не') or stem[-1] in VOWELS)
            and self.nominal_chain(stem+suffix,rest[len(suffix):],depth+1) for suffix in NOUN_SUFFIXES)

    def supported_root_change(self, word, position):
        for analysis in self.analyses(word):
            if position < len(analysis[0]):
                return True
            if len(analysis)==3 and position < len(analysis[0])+len(analysis[1]):
                return True
        return False

    def lexical_root_change(self,word,position,nominal=False):
        """An attested root plus its inflection, including mixed-harmony roots."""
        return any(position<split and any((tag.startswith('N') and not tag.startswith('NP-')) if nominal else not tag.startswith('NP-') for tag in self.lexicon.get(word[:split],()))
                   and (self.nominal_chain(word[:split],word[split:]) if nominal else self.suffix_chain(word[:split],word[split:]))
                   for split in range(max(3 if nominal else 2,position+1),len(word)+1))

    def final_n_candidates(self,word):
        """Genitive/person nasal slots; never infer person from token counts."""
        if not word.endswith('н'):
            return set()
        # Suppletive singular personal-pronoun stems before the genitive.
        if word in ('менін','сенін','онын'):
            return {word[:-1]+'ң'}
        # A past participle may share its prefix with an unrelated vowel-final
        # noun. Analyze the verb before interpreting final н as possession;
        # short verb stems are valid even below the corpus-root length gate.
        for ending in ('ған','ген','қан','кен','атын','етін','йтын','йтін'):
            if word.endswith(ending):
                stem=word[:-len(ending)]
                if stem and self.verb_stem(stem) and compatible(stem,ending):
                    return set()
        # Instrumental case and a nominalized participle with third-person
        # possessive + accusative are valid final-н forms. A free suffix-chain
        # parser alone cannot distinguish them from a malformed genitive.
        if len(word)>3 and word[-3:] in ('мен','бен','пен'):
            stem=word[:-3]
            case='пен' if stem[-1] in VOICELESS else 'бен' if stem[-1] in 'жз' else 'мен'
            if word.endswith(case) and self.lexical_valid(stem):
                return set()
        if word.endswith(('сын','сін')) and len(word)>3:
            root=word[:-3]
            if root[-1] in FRONT|BACK and self.lexical_valid(root):
                return set()
        if word.endswith(('ын','ін')):
            base=word[:-2]
            if any(t.startswith(('N1','N2','N3','N4','N5')) for t in self.lexicon.get(base,())):
                return set()
            for participle in ('ған','ген','қан','кен','атын','етін','йтын','йтін'):
                if base.endswith(participle) and self.verb_stem(base[:-len(participle)]):
                    return set()
        if len(word)>=5 and word[-3:] in ('нын','нін','дын','дін','тын','тін'):
            stem=word[:-3]
            side=harmony(stem) or loan_harmony(stem)
            onset='н' if stem[-1] in (VOWELS-frozenset('у'))|frozenset('нмң') else 'т' if stem[-1] in VOICELESS else 'д'
            ending=onset+('ің' if side=='front' else 'ың')
            if side and word[-3:-1]==ending[:-1] and (self.lexical_valid(stem) or self.valid(stem) or self.protected(stem) or stem in self.excluded):
                return {stem+ending}
        stem=word[:-1]
        if stem and stem[-1] in FRONT|BACK and any(t.startswith('N') for t in self.lexicon.get(stem,())):
            # A bare vowel-final noun takes ң. An existing third-person
            # possessive + accusative remains valid and is not this slot.
            possessive_base=word[:-2] if word[-2:] in ('ын','ін') else ''
            if not possessive_base or not self.lexical_valid(possessive_base):
                return {stem+'ң'}
        return set()

    @lru_cache(maxsize=4096)
    def verb_stem(self,stem):
        if any(tag.startswith('V-') for tag in self.lexicon.get(stem,())):
            return True
        # Productive negative and voice suffixes; recurse only while shrinking.
        return any(stem.endswith(suffix) and len(stem)>len(suffix)+1 and self.verb_stem(stem[:-len(suffix)])
                   for suffix in ('ма','ме','ба','бе','па','пе','л','ыл','іл','н','ын','ін','дыр','дір','тыр','тір','ғыз','гіз','қыз','кіз'))

    def supported_suffix_change(self,word,position):
        # Suffix spelling is constrained even when the loan/native stem occurs
        # only once. This does not lower the frequency bar for root corrections.
        return any(self.frequencies[word[:split]]>=1 and self.suffix_chain(word[:split],word[split:])
                   for split in range(3,position+1))

    def repair_suffix_chain(self,stem,rest):
        if self.suffix_chain(stem,rest):
            return rest
        # A second consonant-assimilation defect must not survive a suggested
        # correction (e.g. a voiced case ending after a voiceless suffix).
        choices=set()
        if stem[-1] in VOICELESS:
            for suffix in SUFFIXES:
                if suffix[0] not in 'дб' or not rest.startswith(suffix):
                    continue
                fixed={'д':'т','б':'п'}[suffix[0]]+suffix[1:]
                tail=rest[len(suffix):]
                if fixed in SUFFIXES and compatible(stem,fixed) and self.suffix_chain(stem+fixed,tail):
                    choices.add(fixed+tail)
        return next(iter(choices)) if len(choices)==1 else None

    def protected(self, word):
        # A legitimate loan/native stem stays legitimate with regular suffixes.
        return any(word.startswith(stem) and self.suffix_chain(stem,word[len(stem):])
                   for stem in self.excluded if len(stem)>=3)

    def back_suffix_candidates(self,word):
        """Correct velars in back-harmony suffixes, independent of root counts.

        A complete valid lexical word wins. Unattested stems require native
        phonotactics: no Russian-only letters, front vowels or velars in the
        back-vowel stem. This excludes loans at the stem boundary.
        """
        result=set()
        for split in range(2,len(word)):
            stem,tail=word[:split],word[split:]
            attested_native=any(tag.startswith(('V-','N1','N2','N3','N4','A','NP-')) for tag in self.lexicon.get(stem,()))
            native_shape=not re.search('[әөүіеэёюфвцчщъьгк]',stem) and stem[0] not in 'рл' and not (stem[0] not in VOWELS and stem[1] not in VOWELS)
            if harmony(stem)!='back' or not (native_shape or attested_native):
                continue
            if stem in self.loans or 'N5' in self.lexicon.get(stem,()):
                continue
            for suffix in SUFFIXES:
                if not set(suffix)&set('ғқ') or not compatible(stem,suffix):
                    continue
                bad=suffix.replace('ғ','г').replace('қ','к')
                if tail.startswith(bad):
                    rest=self.repair_suffix_chain(stem+suffix,tail[len(bad):])
                    if rest is not None:
                        result.add(stem+suffix+rest)
        return result

    def possessive_candidates(self,word):
        """A malformed ending on an independently known stem, never the loan."""
        if not word.endswith(('и','й')):
            return set()
        stem=word[:-1]
        glide_shape=word.endswith('и') and stem.endswith(('ей','ай','ой'))
        if stem in UNIT_SYMBOLS or len(stem)<3 or not (stem in self.loans or 'N5' in self.lexicon.get(stem,()) or glide_shape):
            return set()
        side=loan_harmony(stem)
        if side is None:
            return set()
        onset='с' if stem[-1] in 'аәеіыоөұүэяё' else ''
        surface=stem[:-1] if stem.endswith('ь') else stem
        return {surface+onset+('ы' if side=='back' else 'і')}

    def front_final_i_candidates(self,word):
        """Final high vowel in a front nominal possessive or verb ending."""
        if not word.endswith('и') or len(word)<2 or word[-2] in VOWELS or harmony(word[:-1])!='front':
            return set()
        fixed=word[:-1]+'і'
        stem=word[:-1]
        roots={stem}
        if stem[-1] in 'гғб':
            roots.add(stem[:-1]+{'г':'к','ғ':'қ','б':'п'}[stem[-1]])
        if fixed in self.lexicon or any(any(tag.startswith(('N1','N2','N3','N4')) for tag in self.lexicon.get(root,())) for root in roots):
            return {fixed}
        if word.endswith(('ди','ти')):
            stem=word[:-2]
            if self.verb_stem(stem) or stem.endswith(('е','й')) and self.verb_stem(stem[:-1]):
                return {fixed}
        return set()

    def front_suffix_i_candidates(self,word):
        """Native high vowels remain і throughout a productive suffix chain."""
        result=set()
        if self.lexical_valid(word):
            return result
        for split in range(2,len(word)):
            stem,tail=word[:split],word[split:]
            if 'и' not in tail[:-1] or harmony(stem)!='front':
                continue
            fixed=tail.replace('и','і');roots={stem}
            if fixed[0] in VOWELS and stem[-1] in 'ғгб':
                roots.add(stem[:-1]+{'ғ':'қ','г':'к','б':'п'}[stem[-1]])
            native=any(any(tag.startswith(('N1','N2','N3','N4','V-','A'))
                       for tag in self.lexicon.get(root,())) for root in roots)
            verb=any(self.verb_stem(root) for root in roots)
            nominal_chain=native and self.front_nominal_suffix(stem,fixed)
            converb=(verb and stem[-1] not in VOWELS and fixed.startswith('іп')
                     and fixed[2:] in ('','пін','сің','піз','сіз','ті','тім','тің','тік','тіңіз','сіңдер','сіздер'))
            if nominal_chain or converb:
                result.add(stem+fixed)
        return result

    @lru_cache(maxsize=16384)
    def front_nominal_suffix(self,stem,tail,plural=True,derivation=True):
        """Derivation -> plural -> possessive -> one case, never two cases."""
        cases=('нің','дің','тің','ге','ке','де','те','нен','ден','тен','ні','ді','ті','мен','бен','пен')
        if not tail or tail in cases and compatible(stem,tail):
            return True
        for possessive in ('ім','ің','і','іміз','іңіз','м','ң','сі','міз','ңіз'):
            if not tail.startswith(possessive):
                continue
            vowel_final=stem[-1] in FRONT|BACK
            if vowel_final!=(possessive in ('м','ң','сі','міз','ңіз')):
                continue
            rest=tail[len(possessive):]
            endings=('нің','не','нде','нен','н','мен') if possessive in ('і','сі') else cases
            if not rest or rest in endings and compatible(stem+possessive,rest):
                return True
        if plural:
            for suffix in ('лер','дер','тер'):
                if tail.startswith(suffix) and compatible(stem,suffix) and self.front_nominal_suffix(stem+suffix,tail[len(suffix):],False,False):
                    return True
        if derivation:
            for suffix in ('лік','дік','тік','лі','ді','ті','ші','сіз','гі','кі'):
                if tail.startswith(suffix) and compatible(stem,suffix) and self.front_nominal_suffix(stem+suffix,tail[len(suffix):],plural,False):
                    return True
        return False


def harmonic_i_candidates(word):
    """Front native stems cannot use Russian и in productive case endings.

    Require a native front letter in the stem and a recognized entire suffix.
    Loans such as такси/жюри and native киім/биік never match this condition.
    """
    candidates = set()
    for split in range(3,len(word)):
        stem,tail = word[:split],word[split:]
        if 'и' not in tail or not set(stem)&set('әөүі') or harmony(stem)!='front':
            continue
        fixed = tail.replace('и','і')
        if fixed in ('нің','дің','тің','ні','ді','ті','ім','ің','іміз','іңіз','лік','дік','тік') and compatible(stem,fixed):
            candidates.add(stem+fixed)
    return candidates
