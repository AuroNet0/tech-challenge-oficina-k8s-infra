import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


PUBLIC_SOURCE_FIXTURES = {
    ("AuroNet0/tech-challenge-oficina-auth", ".env.example"): {
        "sha256": "21de8d01be8ed78e2d870e96025f5ed690a65b50dbe71e4ed0a3c90133f36e9d", "kind": "example"},
    ("AuroNet0/tech-challenge-oficina-billing-service", ".env.example"): {
        "sha256": "4ac3c6bb31cea47407e9e85c10109c53342fd9baaa5101452fcef64bb0f4a617", "kind": "example"},
    ("AuroNet0/tech-challenge-oficina-os-service", "src/test/java/com/oficina/os/infrastructure/identity/ClientAccessAdapterTest.java"): {
        "sha256": "fd025aee7d1046166aac926c7943ab4fcd33c6571ae7f14ffc61404ceaa6641a", "kind": "test_access_key_id",
        "payload_sha256": "614da2a56967e5e08c4cb69bfb231b19f0355d7aa40731ed78bd640d08bc2cc3"},
}


def unsafe_file(name, content, repository=None):
    fixture = PUBLIC_SOURCE_FIXTURES.get((repository, name))
    if fixture is not None and hashlib.sha256(content).hexdigest() != fixture["sha256"]:
        return True
    scanned = content
    if fixture is not None and fixture["kind"] == "test_access_key_id":
        matches = re.findall(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", content)
        if len(matches) != 1 or hashlib.sha256(matches[0]).hexdigest() != fixture["payload_sha256"]:
            return True
        scanned = content.replace(matches[0], b"PUBLIC_NEGATIVE_TEST_ACCESS_KEY_ID", 1)

    lowered = name.lower()
    if lowered.endswith(('.p12', '.pfx', '.jks', '.keystore', '.tfstate', '.tfstate.backup', '.tfvars', '.tfvars.json')):
        return True
    if Path(lowered).name.startswith('.env') and not (fixture is not None and fixture["kind"] == "example"):
        return True
    if re.search(rb'-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----', content):
        return True
    if re.search(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b', scanned):
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
    origin = subprocess.run([git, '-C', str(root), 'remote', 'get-url', 'origin'], capture_output=True, check=False)
    remote = origin.stdout.decode('utf-8').strip() if origin.returncode == 0 else ''
    repository = remote.removeprefix('https://github.com/').removesuffix('.git') if remote.startswith('https://github.com/') else None
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
        rejected += int(unsafe_file(name, content, repository))
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
