import os
import subprocess

from pipeline import configuration
from runtime import ROOT, require


def checkout():
    config, catalog = configuration()
    for service, source in config['services'].items():
        directory = ROOT / 'sources' / service
        environment = dict(os.environ)
        environment['GH_TOKEN'] = environment.get('SOURCE_READ_TOKEN') or environment['GH_TOKEN']
        subprocess.run(['gh', 'repo', 'clone', source['repository'], str(directory), '--', '--no-checkout'],
            env=environment, check=True, capture_output=True, timeout=180)
        subprocess.run(['git', '-C', str(directory), 'checkout', '--detach', source['revision']],
            check=True, capture_output=True, timeout=60)
        result = subprocess.run(['git', '-C', str(directory), 'rev-parse', 'HEAD'],
            check=True, capture_output=True, text=True, timeout=30)
        require(result.stdout.strip() == source['revision'], 'SOURCE_REVISION_REJECTED', service)


if __name__ == '__main__':
    checkout()
