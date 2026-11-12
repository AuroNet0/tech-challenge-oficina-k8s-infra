import json
import os
from pathlib import Path


def finalize(directory, steps, job_status, operation, phase, revision):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    checks = []
    terraform = 'NOT_RUN'
    terraform_stacks = {}
    plan_hash = None
    complete_hash = None
    component_hashes = {}
    details = []
    for path in sorted(directory.glob('*/report.json')):
        try:
            report = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(report, dict) or not isinstance(report.get('checks', []), list):
                raise ValueError('Invalid report schema')
            if not all(isinstance(entry, dict) for entry in report.get('checks', [])):
                raise ValueError('Invalid check schema')
        except (OSError, ValueError):
            checks.append(dict(phase='diagnostic-collection', resource=str(path.relative_to(directory)),
                               status='FAIL', reason='DIAGNOSTIC_REPORT_UNREADABLE',
                               next_action='Inspect the producing step log; retain the original failure and other reports.'))
            continue
        checks.extend(dict(entry, report=path.parent.name) for entry in report.get('checks', []))
        detail = {key: report[key] for key in ('services', 'dependency_assessment', 'checkpoint', 'billable_resources',
            'endpoints', 'environment_ready', 'costs', 'planned_changes', 'authorization_scope', 'data_images',
            'removed_resources', 'preserved_resources') if key in report}
        if detail:
            details.append(dict(report=path.parent.name, **detail))
        details.extend(report.get('execution_details', []))
        terraform_stacks.update(report.get('terraform_stacks', {}))
        if report.get('terraform_result', 'NOT_RUN') != 'NOT_RUN':
            terraform = report['terraform_result']
        plan_hash = report.get('plan_sha256') or plan_hash
        complete_hash = report.get('complete_plan_sha256') or complete_hash
        if report.get('plan_sha256') and not report.get('complete_plan_sha256'):
            component_hashes[path.parent.name] = report['plan_sha256']
        component_hashes.update({path.parent.name + '/' + name: value
            for name, value in report.get('component_plan_hashes', {}).items()})
    for name, step in steps.items():
        if step.get('outcome') == 'failure':
            checks.append(dict(phase=name, resource='GitHub step', status='FAIL', reason='GITHUB_STEP_FAILED',
                               next_action='Open this step in the run log; inspect the associated diagnostic report.'))
    if job_status != 'success' and not any(check.get('status') == 'FAIL' for check in checks):
        checks.append(dict(phase='workflow-setup', resource='GitHub runner', status='FAIL', reason='WORKFLOW_SETUP_FAILED',
                           next_action='Inspect checkout, tools, protected environment and OIDC authentication in the run.'))
    statuses = {check.get('status') for check in checks}
    result = 'FAIL' if 'FAIL' in statuses else 'BLOCKED' if 'BLOCKED' in statuses else 'PASS'
    failures = [check for check in checks if check.get('status') == 'FAIL']
    primary = next((check for check in failures if check.get('phase') != 'cleanup'
                    and not check.get('phase', '').startswith(('endpoint-close', 'checkpoint-'))
                    and check.get('phase') not in ('diagnostic-collection', 'residual-verification-after-failure')
                    and check.get('reason') != 'GITHUB_STEP_FAILED'), failures[0] if failures else None)
    plan_hash = complete_hash or (plan_hash if operation != 'plan' else None)
    report = dict(operation=operation, phase=phase, revision=revision, account='446517826146', region='us-east-1',
                  result=result, terraform_result=terraform, terraform_stacks=terraform_stacks,
                  plan_sha256=plan_hash, complete_plan_sha256=complete_hash, component_plan_hashes=component_hashes,
                  primary_failure=primary, checks=checks, execution_details=details)
    (directory / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    rows = [f'# {operation}: {result}', f'Phase: {phase}; commit: {revision}',
            'AWS account: 446517826146; region: us-east-1', f'Terraform: {terraform}', '']
    for stack, outcome in terraform_stacks.items():
        rows.append(f'Terraform {stack}: {outcome}')
    if plan_hash:
        rows += [f'Plan SHA256: `{plan_hash}`', '']
    if primary:
        rows += [f"Primary failure: {primary['phase']} / {primary.get('reason', 'UNKNOWN')}", '']
    if details:
        rows += ['Execution context (versions, checkpoints and potentially billable resources):',
            '```json', json.dumps(details, indent=2), '```', '']
    rows += ['| Step | Resource | Status | Code | Correction |', '|---|---|---|---|---|']
    for check in checks:
        values = [check.get(key, '') for key in ('phase', 'resource', 'status', 'reason', 'next_action')]
        rows.append('| ' + ' | '.join(str(value).replace('|', '/').replace('\n', ' ') for value in values) + ' |')
        if check.get('detail') or check.get('diagnostics') or check.get('aws_call'):
            rows += ['', '```json', json.dumps({key: check[key] for key in
                     ('detail', 'diagnostics', 'aws_call', 'required_permissions') if key in check}, indent=2), '```', '']
    summary = '\n'.join(rows) + '\n'
    (directory / 'summary.md').write_text(summary, encoding='utf-8')
    return report, summary


if __name__ == '__main__':
    report, summary = finalize('actions-report', json.loads(os.getenv('STEPS_JSON', '{}')),
                               os.getenv('JOB_STATUS', 'failure'), os.getenv('OPERATION', 'unknown'),
                               os.getenv('PHASE', 'infra'), os.getenv('GITHUB_SHA', 'unknown'))
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(summary)
