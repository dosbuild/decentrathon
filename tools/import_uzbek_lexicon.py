"""Extract public Uzbek entries without checking out Windows-reserved paths."""
import argparse
import gzip
import hashlib
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

REVISION='1bdee4a38c357226176d516ae533c43f8ecb045b'
parser=argparse.ArgumentParser();parser.add_argument('--repo',type=Path,default=Path('.locqa/apertium-uzb'))
parser.add_argument('--out',type=Path,default=Path('src/dalel/assets/uzbek'));args=parser.parse_args()
def read(name):return subprocess.check_output(['git','-C',str(args.repo),'show',REVISION+':'+name])
raw=read('apertium-uzb.uzb.lexc');entries=defaultdict(set);glossed=defaultdict(set)
for line in raw.decode('utf8').splitlines():
    match=re.match(r"^([A-Za-zʻʼ’‘'`-]+):[^\s]+\s+([A-Z0-9_-]+)\s*;",line)
    if match and match[2] not in ('MIST','Err/Orth','CA'):
        entries[match[1].casefold()].add(match[2])
        if not match[2].startswith(('NP-','ABBR')) and re.search(r'!\s*"[^"\s][^"]+"',line):
            glossed[match[1].casefold()].add(match[2])
args.out.mkdir(parents=True,exist_ok=True)
output=''.join(word+'\t'+','.join(sorted(tags))+'\n' for word,tags in sorted(entries.items()))
(args.out/'lexicon.tsv').write_bytes(output.encode('utf8'))
(args.out/'glossed.tsv').write_bytes(''.join(word+'\t'+','.join(sorted(tags))+'\n' for word,tags in sorted(glossed.items())).encode('utf8'))
(args.out/'upstream.lexc.gz').write_bytes(gzip.compress(raw,mtime=0))
for filename in ('COPYING','AUTHORS','README'):(args.out/filename).write_bytes(read(filename).replace(b'\r\n',b'\n'))
notice={'project':'Apertium Uzbek','source':'https://github.com/apertium/apertium-uzb',
    'revision':REVISION,'licence':'GPL-3.0; upstream COPYING and author notices included',
    'source_sha256':hashlib.sha256(raw).hexdigest(),'lexicon_sha256':hashlib.sha256(output.encode()).hexdigest(),
    'entries':len(entries),'glossed_entries':len(glossed),'transformation':'Latin-script lexical lemmas and continuation tags, case-folded and deduplicated; MIST/CA entries excluded. Candidate subset requires an explicit nonempty upstream gloss and excludes proper names. This is not a human-review claim. Upstream source retained compressed.',
    'private_corpus_inputs':False,'extractor':'tools/import_uzbek_lexicon.py',
    'windows_note':'Use git show, since upstream contains the reserved filename PRN.prefix.dix.'}
(args.out/'SOURCE.json').write_bytes((json.dumps(notice,indent=2)+'\n').encode('utf8'))
print(json.dumps(notice))
