import json
import os

from finalize import finalize


if __name__ == '__main__':
    results = json.loads(os.getenv('JOB_RESULTS', '{}'))
    steps = {name: dict(outcome='failure') for name, job in results.items()
        if job.get('result') in ('failure', 'cancelled')}
    steps.update(json.loads(os.getenv('STEPS_JSON', '{}')))
    report, summary = finalize('all-diagnostics', steps,
        'failure' if any(step.get('outcome') == 'failure' for step in steps.values()) else os.getenv('JOB_STATUS', 'success'),
        os.getenv('OPERATION', 'unknown'), 'complete', os.getenv('GITHUB_SHA', 'unknown'))
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(summary)
    raise SystemExit(1 if report['result'] in ('FAIL', 'BLOCKED') else 0)
