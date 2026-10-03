"""Compatibility entrypoint. Masters now live in design/; do not create local copies."""
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__':
    root = Path(__file__).resolve().parents[2]
    raise SystemExit(subprocess.call([sys.executable, str(root / 'scripts/build_design.py'),
                                      '--render-brand', *sys.argv[1:]], cwd=root))
