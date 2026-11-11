import os
import re

from pipeline import configuration
from runtime import require


config, catalog = configuration()
with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
    for service in ('auth', 'os', 'execution', 'billing'):
        revision = config['services'][service]['revision']
        require(re.fullmatch('[0-9a-f]{40}', revision), 'INVALID_SOURCE_REVISION', service)
        output.write(f'{service}={revision}\n')
