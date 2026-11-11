"""Mixed alphabet evidence and reversible Uzbek character proposals."""
import re

CYRILLIC = re.compile('[А-Яа-яЁёЎўҒғҚқҲҳ]')
LATIN_FOR_CYRILLIC = dict(zip('абвгдежзийклмнопрстуфх', 'abvgdejziyklmnoprstufx'))
LATIN_FOR_CYRILLIC.update({'ё': 'yo', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'ў': 'oʻ', 'ғ': 'gʻ', 'қ': 'q', 'ҳ': 'h', 'ъ': 'ʼ'})


def mixed_alphabet(source, target, message, locale):
    from dalel.languages.identify import script
    if script(locale) != 'LATIN':
        return None
    edits, letters = [], set()
    safe = True
    for start, end in message.text_spans:
        visible = re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+|<[^>]*>|\b\w*[\d_]\w*\b', lambda m: ' ' * len(m[0]), target[start:end])
        for match in re.finditer(r"[^\W\d_]+(?:['ʻʼ’][^\W\d_]+)*", visible):
            token = match[0]
            found = CYRILLIC.findall(token)
            if not found or not re.search('[A-Za-z]', token) or token in source:
                continue
            letters.update(found)
            replacement = []
            for char in token:
                converted = LATIN_FOR_CYRILLIC.get(char.lower()) if CYRILLIC.fullmatch(char) else char
                if converted is None:
                    converted, safe = char, False
                elif char.isupper():
                    converted = converted.upper() if token.isupper() else converted.capitalize()
                replacement.append(converted)
            edits.append((start + match.start(), start + match.end(), token, ''.join(replacement)))
    if not edits:
        return None
    corrected = target
    for start, end, _, replacement in reversed(edits):
        corrected = corrected[:start] + replacement + corrected[end:]
    description = ', '.join(f'{char} (U+{ord(char):04X})' for char in sorted(letters))
    return dict(suggestion=corrected if safe else None, tokens=[e[2] for e in edits], letters=sorted(letters),
                evidence='В латинском слове «' + edits[0][2][:65] + '» встречается кириллица: ' + description[:140] + '. Правило: не смешивать алфавиты внутри слова.')
