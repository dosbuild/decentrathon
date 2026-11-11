"""Reproducibly extract a general public lexicon; never read case/data/judge."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

REVISION='95c6dd0d8536ee69a7058634b03a3e82100b6b6e'
checkout=Path('.locqa/apertium-kaz')
output=Path('src/dalel/assets/kazakh')
output.mkdir(parents=True,exist_ok=True)
def blob(name):
    return subprocess.check_output(['git','-C',str(checkout),'show',REVISION+':'+name])
raw=blob('apertium-kaz.kaz.lexc')
entries={}
for line in raw.decode('utf8').splitlines():
    match=re.match(r'^([А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]+):[^\s]+\s+([A-Z0-9_-]+)\s*;',line)
    if match:
        word,tag=match.groups()
        entries.setdefault(word.casefold(),set()).add(tag)
payload=''.join(word+'\t'+','.join(sorted(tags))+'\n' for word,tags in sorted(entries.items()))
(output/'lexicon.tsv').write_bytes(payload.encode('utf8'))
for name in ('AUTHORS','COPYING'):
    (output/name).write_bytes(blob(name))
metadata={'source':'https://github.com/apertium/apertium-kaz','revision':REVISION,
          'source_file':'apertium-kaz.kaz.lexc','source_sha256':hashlib.sha256(raw).hexdigest(),
          'entries':len(entries),'license':'GPL-3.0-only',
          'extraction':'All unescaped single Cyrillic-word canonical left-side entries, with continuation tags; no task corpus filtering.',
          'sha256':hashlib.sha256(payload.encode('utf8')).hexdigest()}
(output/'provenance.json').write_bytes((json.dumps(metadata,ensure_ascii=False,indent=2)+'\n').encode('utf8'))
print(json.dumps(metadata))
