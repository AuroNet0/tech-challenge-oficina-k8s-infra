import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


def unsafe_file(name, content):
    lowered = name.lower()
    if lowered.endswith(('.p12', '.pfx', '.jks', '.keystore', '.tfstate', '.tfstate.backup', '.tfvars', '.tfvars.json')):
        return True
    if Path(lowered).name.startswith('.env'):
        return True
    if re.search(rb'-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----', content):
        return True
    if re.search(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b', content):
        return True
    if re.search(rb'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{20,}', content):
        return True
    if lowered.endswith(('.yaml', '.yml')) and re.search(rb'^\s*kind:\s*Secret\s*$', content, re.MULTILINE):
        return True
    if lowered.endswith('.json'):
        try:
            document = json.loads(content)
            objects = document.get('items', [document]) if isinstance(document, dict) else document
            if isinstance(objects, list) and any(isinstance(item, dict) and item.get('kind') == 'Secret' for item in objects):
                return True
        except (ValueError, UnicodeDecodeError):
            pass
    return False


def scan(root, git='git', staged=False, paths=None):
    root = Path(root).resolve()
    arguments = [git, '-C', str(root), 'diff', '--cached', '--name-only', '-z', '--diff-filter=ACMR'] if staged else [git, '-C', str(root), 'ls-files', '--cached', '--others', '--exclude-standard', '-z']
    if paths is None:
        result = subprocess.run(arguments, capture_output=True, check=True)
        paths = result.stdout.decode('utf-8').split('\0')
    rejected = 0
    checked = 0
    for name in set(paths) - {''}:
        target = root / name
        if not staged and not target.exists():
            continue
        if not target.resolve().is_relative_to(root) or target.is_symlink():
            rejected += 1
            continue
        content = subprocess.run([git, '-C', str(root), 'show', f':{name}'], capture_output=True, check=True).stdout if staged else target.read_bytes()
        rejected += int(unsafe_file(name, content))
        checked += 1
    print(f'Secret file check: {checked} files; {rejected} rejected; payloads suppressed.')
    return 1 if rejected else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Conservative local Git guard; prints counts only.')
    parser.add_argument('--root', required=True)
    parser.add_argument('--git', default='git')
    parser.add_argument('--staged', action='store_true')
    parser.add_argument('--paths-stdin', action='store_true')
    options = parser.parse_args()
    try:
        if options.paths_stdin and options.staged:
            raise ValueError('Invalid scan mode')
        paths = json.load(sys.stdin) if options.paths_stdin else None
        sys.exit(scan(options.root, options.git, options.staged, paths))
    except Exception:
        print('SECRET_FILE_CHECK_REJECTED', file=sys.stderr)
        sys.exit(1)
