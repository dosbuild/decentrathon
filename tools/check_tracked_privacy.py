"""Check tracked paths without opening or displaying credentials/client data."""
import subprocess
import sys

paths = subprocess.check_output(['git','ls-files','-z']).decode('utf-8').split('\0')
private_roots=('data/','out/','.locqa/','demo/','examples/','tests/fixtures/','desktop-web-app/','profiles/',
               'src/dalel/assets/workspace/demo/','src/dalel/assets/workspace/src/demo.js')
violations = [p for p in paths if p and (p=='.env' or p.startswith(private_roots))]
print(f'Tracked private paths: {len(violations)}')
sys.exit(bool(violations))
