"""Conservative sibling reference discovery; generated fixed files are excluded."""
from pathlib import Path
from dalel.formats.arb import ArbDocument

def discover(source,target):
    base=lambda locale:locale.replace('-','_').split('_')[0]
    excluded={base(source.locale),base(target.locale)};result={}
    directories=dict.fromkeys(doc.path.resolve().parent for doc in (source,target) if doc.path)
    for directory in directories:
        for path in sorted(directory.glob('intl_*.arb')):
            doc=ArbDocument.read(path)
            if path.stem not in ('intl_'+doc.locale,'intl_'+doc.locale.replace('-','_')):continue
            if base(doc.locale) in excluded:continue
            if doc.locale in result and result[doc.locale].raw!=doc.raw:
                raise ValueError('Conflicting sibling references for locale '+doc.locale+'; use explicit --ref')
            result.setdefault(doc.locale,doc)
    return [result[k] for k in sorted(result)]
