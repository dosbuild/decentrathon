"""Screen short labels for added instructions, without inferring screen layout."""
import re

PADDING = re.compile(r'\s+(?:(?:бөліміне|бетіне)\s+өту\s+үшін\s+басыңыз|үшін\s+басыңыз|туралы\s+(?:толық\s+)?ақпарат\s+алу\s+үшін|режимін\s+қосу\s+және\s+жалғастыру)[.!]?\s*$', re.I)
INSTRUCTION = re.compile(r'нажм|чтобы|перей|информац|click|press|information', re.I)


def inspect_label(source, target, source_locale, target_locale):
    from dalel.engine.rules import lexical_text
    if (source_locale.split('_')[0], target_locale.split('_')[0]) != ('ru', 'kk'):
        return None
    original, translated = (lexical_text(value).strip() for value in (source, target))
    if not original or len(original) > 32 or len(original.split()) > 4 or INSTRUCTION.search(original):
        return None
    if len(translated) <= 1.8 * len(original) or len(translated) - len(original) <= 12:
        return None
    tail = PADDING.search(target)
    if tail is None:
        return None
    label = target[:tail.start()]
    if not label.strip() or any(char in label for char in '{}%\n:;!?') or len(lexical_text(label).split()) > 4:
        return None
    adds_meaning = bool(re.search('бөліміне|бетіне|туралы|режимін|жалғастыру', tail[0], re.I))
    return dict(suggestion=label, source_length=len(original), target_length=len(translated),
                issue_type='machine_translation_suspect' if adds_meaning else 'length_overflow',
                severity='medium' if adds_meaning else 'low',
                evidence=('Добавлено отсутствующее действие или объект. ' if adds_meaning else 'Добавлен только призыв нажать. ') +
                f'Короткая надпись раздута инструкцией «{tail[0].strip()}»: {len(translated)} символов против {len(original)} в исходнике. Правило: отношение > 1.8 и избыток > 12; достаточно надписи без инструкции.')
