import argparse
import base64
import json
import os
from pathlib import Path
import re
import sys
import time
import xml.etree.ElementTree as ET
import urllib.parse
import urllib.request

import pipeline
from runtime import ACCOUNT, PREFIX, REGION, ROOT, Failure, Report, canonical, digest, require, safe_diagnostic


BLOCKS = (
    ('identities', 'bootstrap', ('iam-maintenance',)),
    ('infra', 'provision', ('infra',)),
    ('recovery', 'bootstrap', ('iam-maintenance', 'recovery-closed', 'recovery-activate', 'recovery-expiry')),
    ('release', 'deploy', ('release',)),
    ('foundation', 'provision', ('platform',)),
    ('platform-iam', 'bootstrap', ('iam-maintenance',)),
    ('controllers', 'provision', ('foundation', 'controller')),
    ('data', 'deploy', ('secrets', 'data')),
    ('migration-plan', 'provision', ('platform-migration',)),
    ('migration', 'deploy', ('migrate',)),
    ('auth-plan', 'provision', ('edge-auth',)),
    ('edge-iam', 'bootstrap', ('iam-maintenance',)),
    ('jwks', 'deploy', ('jwks',)),
    ('apps-plan', 'provision', ('platform-apps',)),
    ('apps', 'deploy', ('apps',)),
    ('ingress', 'provision', ('edge-alb', 'platform-apps', 'controller', 'ingress')),
    ('targets', 'deploy', ('alb-check',)),
    ('payment-config', 'deploy', ('payment-config',)),
    ('routes', 'provision', ('edge-business',)),
    ('observability', 'provision', ('observability',)),
    ('verify', 'deploy', ('health', 'e2e')),
)

DEFERRED_LIMITS = dict(aws_lambda_function=3, aws_apigatewayv2_api=1, aws_apigatewayv2_vpc_link=1,
    aws_vpc_endpoint=1, aws_eks_addon=3, aws_eks_pod_identity_association=2, aws_s3_bucket=1,
    aws_dynamodb_table=1, aws_apigatewayv2_route=50)


def configuration_hash(config, catalog):
    return digest(canonical(dict(configuration=config, catalog=catalog)))


def record(report, status, resource, reason):
    report.data['checks'].append(dict(phase='complete-readiness', resource=resource, status=status,
        reason=reason, duration_ms=0, next_action='Configure o requisito indicado e execute novamente o Plan.'))
    report.write()


def prerequisites(config, report):
    if not config.get('auth_signing_secret_arn'):
        record(report, 'BLOCKED', 'AUTH_SIGNING_SECRET_ARN', 'PERSISTENT_SIGNING_IDENTITY_REQUIRED; deploy cannot create/read public signing key')
    for phase in ('infra', 'release', 'platform', 'foundation', 'secrets', 'observability'):
        try:
            pipeline.technical_inputs(config, phase)
        except Failure as error:
            record(report, 'BLOCKED', error.resource or phase, error.code)
    for field in ('certificate_arn', 'server_name'):
        if not config['terraform_inputs']['edge'].get('alb_tls', {}).get(field):
            record(report, 'BLOCKED', 'ALB_' + field.upper(), 'HTTPS_ALB_CONFIGURATION_REQUIRED')
    payment = config['terraform_inputs']['platform'].get('mercado_pago', {})
    for name in ('success', 'failure', 'pending'):
        url = payment.get(name + '_url', '')
        if not re.fullmatch(r'https://[A-Za-z0-9.-]+/[^@?# ]*', url) or '.invalid/' in url:
            record(report, 'BLOCKED', 'MERCADO_PAGO_' + name.upper() + '_URL', 'REAL_HTTPS_RETURN_URL_REQUIRED')
    if not os.getenv('E2E_FIXTURE_SECRET_ARN'):
        record(report, 'BLOCKED', 'E2E_FIXTURE_SECRET_ARN', 'DEMO_IDENTITIES_AND_GRANTS_REQUIRED')
    record(report, 'DEFERRED', 'AWS runtime', 'READINESS_SAGA_PAYMENT_AND_TELEMETRY_REQUIRE_DEPLOY')


def sources(config, report):
    for service, source in config['services'].items():
        with report.phase('source-and-current-ci', service):
            repository, revision = source['repository'], source['revision']
            head = pipeline.gh_json([f'repos/{repository}/git/ref/heads/homolog'], source=True)
            require(head['object']['sha'] == revision, 'HOMOLOG_CHANGED_UPDATE_SOURCE_LOCK', service)
            pages = pipeline.gh_json(['--paginate', '--slurp',
                f'repos/{repository}/commits/{revision}/check-runs?per_page=100'], source=True)
            checks = [check for page in pages for check in page['check_runs']]
            for name in source['required_checks']:
                matches = sorted((check for check in checks if check['name'] == name
                    and check.get('app', {}).get('slug') == 'github-actions'), key=lambda check: check['id'])
                require(matches and matches[-1]['head_sha'] == revision
                    and matches[-1]['status'] == 'completed' and matches[-1]['conclusion'] == 'success',
                    'CURRENT_REVISION_CI_REQUIRED', service + ':' + name)
            analysis_checks = {check['name']: check for check in sorted(checks, key=lambda check: check['id'])
                if re.search(r'Sonar|CodeQL|Analyze \(java|PMD|SpotBugs', check['name'], re.I)
                and check.get('app', {}).get('slug') in ('github-actions', 'github-code-scanning', 'sonarcloud', 'sonarqubecloud')}
            analysis = [check for check in analysis_checks.values() if check['head_sha'] == revision
                and check['status'] == 'completed' and check['conclusion'] == 'success']
            if not analysis:
                record(report, 'BLOCKED', service, 'FIAP_STATIC_QUALITY_ANALYSIS_EVIDENCE_REQUIRED_FOR_THIS_SHA')
            from runtime import module_at
            builder = module_at('complete_artifact_contract', 'scripts/artifact-release', 'release.py')

            def source_file(path):
                document = pipeline.gh_json([f'repos/{repository}/contents/{path}?ref={revision}'], source=True)
                require(document.get('encoding') == 'base64', 'SOURCE_FILE_ENCODING_REJECTED', path)
                return base64.b64decode(document['content']).decode()

            builder.dockerfile_contract(source_file('Dockerfile'), service)
            pom = ET.fromstring(source_file('pom.xml'))
            namespaces = {'m': 'http://maven.apache.org/POM/4.0.0'}
            jacoco = [plugin for plugin in pom.findall('.//m:plugin', namespaces)
                if plugin.findtext('m:artifactId', namespaces=namespaces) == 'jacoco-maven-plugin']
            require(jacoco and any('check' in [goal.text for goal in execution.findall('m:goals/m:goal', namespaces)]
                and any(float(limit.findtext('m:minimum', default='0', namespaces=namespaces)) >= 0.8
                    and limit.findtext('m:counter', default='LINE', namespaces=namespaces) == 'LINE'
                    and limit.findtext('m:value', default='COVEREDRATIO', namespaces=namespaces) == 'COVEREDRATIO'
                    for limit in execution.findall('.//m:limit', namespaces))
                for plugin in jacoco for execution in plugin.findall('.//m:execution', namespaces)),
                'JACOCO_80_PERCENT_GATE_REQUIRED', service)
            report.data.setdefault('services', {})[service] = dict(source, check_ids=[check['id'] for check in checks])


def driver_for(config, catalog, report, phase):
    if phase in pipeline.MAINTENANCE_PHASES:
        driver = pipeline.MaintenanceDriver(config, report, phase, catalog=catalog)
    else:
        driver = pipeline.PipelineDriver(config, report, catalog=catalog)
    driver.load()
    driver.authorization_source = 'github-environment'
    return driver


def inspect_images(driver):
    images = dict(driver.config['terraform_inputs']['platform'].get('images', {}),
        network_probe=driver.config.get('network_probe_image', ''))
    for name in ('mongodb', 'rabbitmq', 'postgres', 'network_probe'):
        reference = images.get(name, '')
        if not re.fullmatch(r'[a-z0-9./:_-]+@sha256:[0-9a-f]{64}', reference):
            continue
        with driver.report.phase('pinned-image-readback', name):
            driver.run(['docker', 'pull', '--platform=linux/amd64', reference], timeout=600)
            document = json.loads(driver.run(['docker', 'image', 'inspect', reference]))[0]
            require(document.get('Architecture') == 'amd64' and document.get('Os') == 'linux', 'DATA_IMAGE_PLATFORM_REJECTED', name)
            expected_digest = reference.split('@', 1)[1]
            require(any(item.endswith('@' + expected_digest) for item in document.get('RepoDigests', [])), 'DATA_IMAGE_DIGEST_MISMATCH', name)
            driver.report.data.setdefault('data_images', {})[name] = dict(reference=reference, platform='linux/amd64', readback_verified=True)


def prepare_identities(driver):
    driver.maintenance_inputs.update(artifact_bucket_installation_approved=True, complete_workflow_delegation_enabled=True,
        recovery_installation_approved=True, recovery_package_path='endpoint-recovery.zip',
        recovery_alarm_topic_arn=os.getenv('RECOVERY_ALARM_TOPIC_ARN', '')
            or driver.maintenance_inputs.get('recovery_alarm_topic_arn', ''))


def plan_part(config, catalog, report, role):
    require(role in ('bootstrap', 'provision', 'deploy'), 'EXECUTOR_ROLE_REJECTED')
    require(os.environ['EXECUTOR_ROLE'] == role, 'EXECUTOR_ROLE_REJECTED')
    if role == 'deploy':
        driver = driver_for(config, catalog, report, 'health')
        try:
            from aws_e2e import validate_fixture
            statuses = {}
            for field, kind in (('mercado_pago_test_secret_arn', 'mercado-pago'),
                                ('e2e_fixture_secret_arn', 'e2e-fixture')):
                with report.phase('external-runtime-input', kind) as check:
                    secret = config.get(field, '')
                    require(re.fullmatch(fr'arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:/oficina/phase4/demo-a/inputs/{kind}-[A-Za-z0-9]{{6}}', secret),
                        'EXTERNAL_SECRET_SCOPE_REJECTED', kind)
                    try:
                        value = json.loads(driver.api('secretsmanager', 'get_secret_value', SecretId=secret)['SecretString'])
                    except Failure as error:
                        if error.code not in ('AccessDenied', 'AccessDeniedException'):
                            raise
                        check.update(status='DEFERRED', reason='SECRET_READ_DEPENDS_ON_REVIEWED_IDENTITY_PLAN')
                        statuses[kind] = 'DEFERRED'
                        continue
                    if kind == 'e2e-fixture':
                        validate_fixture(value)
                    else:
                        require(value.get('access-token', '').startswith('TEST-') and value.get('webhook-secret'),
                            'MERCADO_PAGO_TEST_CREDENTIAL_REQUIRED')
                    statuses[kind] = 'PASS'
            (report.directory / 'part.json').write_bytes(canonical(dict(runtime_inputs=statuses,
                configuration_sha256=configuration_hash(config, catalog))))
        finally:
            driver.temporary.cleanup()
        return
    phase = 'iam-maintenance' if role == 'bootstrap' else 'infra'
    driver = driver_for(config, catalog, report, phase)
    try:
        version = json.loads(driver.run(['terraform', 'version', '-json']))
        require(version['terraform_version'] == '1.15.8', 'TERRAFORM_VERSION_REJECTED')
        if role == 'bootstrap':
            prepare_identities(driver)
        else:
            prerequisites(config, report)
            if config.get('auth_signing_secret_arn'):
                from live_secrets import ensure_signing
                ensure_signing(driver)
            sources(config, report)
            inspect_images(driver)
        pipeline.plan(driver, phase)
        part = dict(summary=json.loads((report.directory / 'plan.json').read_text()),
            configuration_sha256=configuration_hash(config, catalog),
            encrypted=base64.b64encode((report.directory / 'approved-plan.enc').read_bytes()).decode())
        (report.directory / 'part.json').write_bytes(canonical(part))
    finally:
        driver.temporary.cleanup()


def deferred_scope():
    scopes = {}
    for stack in ('platform', 'edge', 'identities'):
        directory = ROOT / ('phase4/bootstrap/identities' if stack == 'identities' else 'phase4/stacks/' + stack)
        paths = list(directory.glob('*.tf'))
        if stack == 'identities':
            paths += list((ROOT / 'phase4/modules/endpoint-recovery').glob('*.tf'))
        scopes[stack] = sorted({family for path in paths for family in
            re.findall(r'resource\s+"([a-z0-9_]+)"\s+"', path.read_text())})
    return scopes


def assemble(config, catalog, report, directory):
    parts = {}
    checks = []
    for role in ('bootstrap', 'provision', 'deploy'):
        part_dir = Path(directory) / role
        parts[role] = json.loads((part_dir / 'part.json').read_text())
        part_report = json.loads((part_dir / 'report.json').read_text())
        require(parts[role].get('configuration_sha256') == configuration_hash(config, catalog)
            and part_report.get('revision') == os.environ['GITHUB_SHA'], 'PLAN_PART_CONTEXT_MISMATCH', role)
        if 'summary' in parts[role]:
            summary = parts[role]['summary']
            component = summary['manifest']
            ciphertext = base64.b64decode(parts[role]['encrypted'], validate=True)
            require(summary['sha256'] == digest(canonical(component) + ciphertext)
                and component['revision'] == os.environ['GITHUB_SHA'] and component['run_id'] == os.environ['GITHUB_RUN_ID']
                and component.get('environment') == 'demo-a' and component.get('account') == ACCOUNT
                and component.get('region') == REGION
                and component.get('stack') == ('identities' if role == 'bootstrap' else 'infra')
                and component.get('phase') == ('iam-maintenance' if role == 'bootstrap' else 'infra'),
                'PLAN_PART_CONTEXT_MISMATCH', role)
            payload = pipeline.unseal(ciphertext, component)
            require(digest(base64.b64decode(payload['binary'], validate=True)) == component['binary_sha256'],
                'APPROVED_PLAN_HASH_MISMATCH', role)
        checks.extend(part_report['checks'])
    bootstrap_states = parts['bootstrap']['summary']['manifest']['states']
    infra_states = parts['provision']['summary']['manifest']['states']
    require(all(bootstrap_states.get(stack) == value for stack, value in infra_states.items()), 'PLAN_PART_STATE_MISMATCH')
    report.data['checks'].extend(checks)
    require(not any(check['status'] in ('FAIL', 'BLOCKED') for check in checks), 'COMPLETE_PLAN_BLOCKED')
    manifest = dict(schema_version=1, operation='complete-deploy', revision=os.environ['GITHUB_SHA'],
        run_id=os.environ['GITHUB_RUN_ID'], account=ACCOUNT, region=REGION, environment='demo-a',
        config_sha256=configuration_hash(config, catalog), services=config['services'],
        blocks=BLOCKS, deferred_scope=deferred_scope(), max_deferred_resources=200, deferred_limits=DEFERRED_LIMITS,
        prerequisite_secret=os.environ['E2E_FIXTURE_SECRET_ARN'],
        dependency_assessment=dict(identities='SAVED_TERRAFORM_PLAN', infra='SAVED_TERRAFORM_PLAN',
            platform='DEFERRED: infra, images, secrets and migrations',
            edge='DEFERRED: platform, Auth artifact, JWKS and healthy ALB'),
        costs='EKS, two m7i-flex.large, RDS db.t4g.micro/20GiB, EBS, ALB, VPC endpoints, logs, secrets and traffic are billable. No free eligibility inferred.')
    manifest['authorization_scope'] = dict(deploy_environment='phase4-demo-a-bootstrap',
        destroy_environment='phase4-demo-a-destroy', primary_immutable_subjects_preserved=True,
        bootstrap_delegates_to=['provision', 'deploy'], destroy_delegates_to=['provision'],
        application_deploy_environment_cannot_assume_bootstrap=True)
    encrypted = pipeline.seal(parts, manifest)
    bundle = dict(manifest=manifest, encrypted=base64.b64encode(encrypted).decode(),
        sha256=digest(canonical(manifest) + encrypted))
    (report.directory / 'bundle.json').write_bytes(canonical(bundle))
    report.data['plan_sha256'] = bundle['sha256']
    report.data['complete_plan_sha256'] = bundle['sha256']
    report.data['dependency_assessment'] = manifest['dependency_assessment']
    report.data['services'] = config['services']
    report.data['costs'] = manifest['costs']
    report.data['authorization_scope'] = manifest['authorization_scope']
    report.data['planned_changes'] = {role: part['summary']['manifest'].get('changes', [])
        for role, part in parts.items() if 'summary' in part}


def read_bundle(config, catalog, reference, approved_hash, path):
    require(re.fullmatch(r'[0-9]+', reference), 'PLAN_RUN_REQUIRED')
    run = pipeline.gh_json([f'repos/{pipeline.REPOSITORY}/actions/runs/{reference}'])
    require(run.get('path') == '.github/workflows/plan.yml' and run.get('event') == 'workflow_dispatch'
        and run.get('head_branch') == 'homolog' and run.get('head_sha') == os.environ['GITHUB_SHA']
        and run.get('conclusion') == 'success', 'PLAN_RUN_PROVENANCE_REJECTED')
    bundle = json.loads(Path(path).read_text())
    manifest = bundle['manifest']
    encrypted = base64.b64decode(bundle['encrypted'], validate=True)
    require(re.fullmatch('[0-9a-f]{64}', approved_hash)
        and approved_hash == bundle['sha256'] == digest(canonical(manifest) + encrypted), 'APPROVED_PLAN_HASH_MISMATCH')
    require(manifest.get('operation') == 'complete-deploy' and manifest.get('run_id') == reference
        and manifest.get('revision') == os.environ['GITHUB_SHA'] and manifest.get('account') == ACCOUNT
        and manifest.get('region') == REGION and manifest.get('environment') == 'demo-a'
        and manifest.get('config_sha256') == configuration_hash(config, catalog)
        and canonical(manifest.get('blocks')) == canonical(BLOCKS)
        and manifest.get('prerequisite_secret') == os.getenv('E2E_FIXTURE_SECRET_ARN'), 'PLAN_CONTEXT_MISMATCH')
    return manifest, pipeline.unseal(encrypted, manifest)


def review_deferred(document, stack, manifest):
    require(not document.get('errored') and not document.get('deferred_changes'), 'PLAN_INCOMPLETE')
    changes = [change for change in document.get('resource_changes', []) if change.get('mode') != 'data']
    require(len(changes) <= manifest['max_deferred_resources'], 'DEFERRED_SCOPE_EXPANSION_REQUIRES_REVIEW')
    limits = manifest.get('deferred_limits', DEFERRED_LIMITS)
    for family, maximum in limits.items():
        require(sum(change.get('type') == family for change in changes) <= maximum,
            'DEFERRED_COST_EXPANSION_REQUIRES_REVIEW', family)
    for change in changes:
        actions = change['change']['actions']
        require(set(actions) <= {'no-op', 'create', 'update', 'read'}, 'DEFERRED_DESTRUCTIVE_CHANGE_REQUIRES_REVIEW', change['address'])
        require(change['type'] in manifest['deferred_scope'][stack], 'DEFERRED_RESOURCE_OUTSIDE_SCOPE', change['address'])
        require(change['type'] not in ('aws_db_instance', 'aws_eks_cluster', 'aws_eks_node_group', 'aws_instance'),
            'DEFERRED_COST_EXPANSION_REQUIRES_REVIEW', change['address'])
        after = change['change'].get('after') or {}
        if change['type'] == 'aws_lambda_function':
            require(isinstance(after.get('memory_size'), int) and after['memory_size'] <= 512
                and isinstance(after.get('reserved_concurrent_executions'), int)
                and 1 <= after['reserved_concurrent_executions'] <= 2, 'DEFERRED_LAMBDA_COST_REQUIRES_REVIEW', change['address'])
        if change['type'] == 'aws_cloudwatch_log_group':
            require(isinstance(after.get('retention_in_days'), int) and 1 <= after['retention_in_days'] <= 30,
                'DEFERRED_LOG_RETENTION_REQUIRES_REVIEW', change['address'])


def apply_part(driver, part, phase, manifest, deferred=False):
    summary = part['summary']
    local_manifest = summary['manifest']
    encrypted = base64.b64decode(part['encrypted'], validate=True)
    require(summary['sha256'] == digest(canonical(local_manifest) + encrypted), 'APPROVED_PLAN_HASH_MISMATCH')
    payload = pipeline.unseal(encrypted, local_manifest)
    stack = local_manifest['stack']
    binary = base64.b64decode(payload['binary'], validate=True)
    require(digest(binary) == local_manifest['binary_sha256'], 'APPROVED_PLAN_HASH_MISMATCH')
    require(local_manifest['states'] == pipeline.state_versions(driver), 'TERRAFORM_STATE_CHANGED_REPLAN_REQUIRED')
    require(local_manifest['contract_sha256'] == pipeline.contract_hash(driver), 'INVENTORY_OR_CONFIGURATION_CHANGED_REPLAN_REQUIRED')
    saved = Path(driver.temporary.name) / 'complete-approved.tfplan'
    saved.write_bytes(binary)
    driver.init(stack)
    if deferred:
        review_deferred(json.loads(driver.tf(stack, ['show', '-json', str(saved)])), stack, manifest)
    if stack == 'identities':
        driver.review(json.loads(driver.tf(stack, ['show', '-json', str(saved)])))
    elif stack == 'infra':
        driver.recovery_ready(opening=False)
    else:
        driver.verify_attestations(stack, payload['variables'])
    if stack != 'identities':
        driver.journal['stacks'][stack] = payload['variables']
        driver.save()
    try:
        driver.tf(stack, ['apply', '-json', '-input=false', '-lock-timeout=60s', str(saved)])
    except BaseException:
        driver.report.data['terraform_result'] = 'FAILED'
        driver.report.data.setdefault('terraform_stacks', {})[stack] = 'APPLY_FAILED'
        if stack != 'identities':
            try:
                with driver.report.phase('checkpoint-after-failed-apply', stack):
                    driver.snapshot(stack)
            except BaseException:
                pass
        raise
    if stack != 'identities':
        driver.snapshot(stack)
    driver.report.data.setdefault('terraform_stacks', {})[stack] = 'APPLIED_APPROVED_BINARY'
    driver.report.data['terraform_result'] = 'APPLIED_APPROVED_BINARY'


def state_context(driver):
    keys = {stack: f'phase4/environments/demo-a/{stack}.tfstate' for stack in pipeline.STACKS}
    original = getattr(driver, 'state_keys', None)
    driver.state_keys = keys
    try:
        return dict(states=pipeline.state_versions(driver), inventory=digest(canonical(driver.journal)))
    finally:
        if original is None:
            del driver.state_keys
        else:
            driver.state_keys = original


def execute_block(config, catalog, report, block_name, reference, approved_hash, artifact, progress):
    manifest, parts = read_bundle(config, catalog, reference, approved_hash, Path(artifact) / 'bundle.json')
    index = next(index for index, block in enumerate(BLOCKS) if block[0] == block_name)
    name, role, phases = BLOCKS[index]
    require(os.environ['EXECUTOR_ROLE'] == role and os.environ['PROTECTED_ENVIRONMENT'] == 'phase4-demo-a-bootstrap',
        'EXECUTOR_ROLE_REJECTED')
    pipeline.protection()
    progress_context = dict(plan_sha256=approved_hash, deploy_run=os.environ['GITHUB_RUN_ID'], revision=os.environ['GITHUB_SHA'])
    expected = None
    if index:
        previous = json.loads(Path(progress).read_text())
        expected = pipeline.unseal(base64.b64decode(previous['encrypted'], validate=True), progress_context)
        require(expected['next_block'] == index, 'CHECKPOINT_ORDER_REJECTED')
    driver = None
    try:
        for phase_index, phase in enumerate(phases):
            report.data['phase'] = phase
            with report.phase('complete-checkpoint', phase):
                driver = driver_for(config, catalog, report, phase)
                if phase_index == 0 and expected:
                    require(state_context(driver) == expected['state_context'], 'CHECKPOINT_STATE_OR_INVENTORY_DRIFT')
                if phase in pipeline.MAINTENANCE_PHASES:
                    prepare_identities(driver)
                if index == 0 or (name == 'infra' and phase == 'infra'):
                    apply_part(driver, parts[role], phase, manifest)
                elif phase in pipeline.MAINTENANCE_PHASES or phase.split('-')[0] in pipeline.STACKS:
                    pipeline.plan(driver, phase)
                    part = dict(summary=json.loads((report.directory / 'plan.json').read_text()),
                        encrypted=base64.b64encode((report.directory / 'approved-plan.enc').read_bytes()).decode())
                    apply_part(driver, part, phase, manifest, deferred=True)
                elif phase in ('recovery-closed', 'recovery-expiry'):
                    from verify_recovery import verify
                    os.environ['VERIFY_MODE'] = 'closed-canary' if phase == 'recovery-closed' else 'lease-expiry'
                    verify(driver)
                else:
                    from delivery import up_phase
                    up_phase(driver, phase)
                report.data['billable_resources'] = [dict(type=resource['type'], id=resource.get('id'), arn=resource.get('arn'))
                    for resources in driver.journal['resources'].values() for resource in resources if resource['type'].startswith('aws_')]
                expected = dict(next_block=index + 1, state_context=state_context(driver))
                driver.temporary.cleanup()
                driver = None
        (report.directory / 'progress.json').write_bytes(canonical(dict(
            encrypted=base64.b64encode(pipeline.seal(expected, progress_context)).decode())))
        report.data['checkpoint'] = dict(completed=name, next_block=index + 1, plan_sha256=approved_hash)
    finally:
        report.data['plan_sha256'] = approved_hash
        if driver:
            report.data['checkpoint'] = dict(block=name, phase=report.data.get('phase'),
                resume='REPLAN_IF_STATE_CHANGED; reuses existing resources and published artifacts', plan_sha256=approved_hash)
            report.data['billable_resources'] = [dict(type=resource['type'], id=resource.get('id'), arn=resource.get('arn'))
                for resources in driver.journal['resources'].values() for resource in resources if resource['type'].startswith('aws_')]
            driver.temporary.cleanup()


def assume(role):
    environment = os.environ.get('PROTECTED_ENVIRONMENT')
    allowed = {'phase4-demo-a-bootstrap': {'bootstrap', 'provision', 'deploy'},
        'phase4-demo-a-destroy': {'provision', 'destroy'}}
    require(role in allowed.get(environment, set()), 'COMPLETE_ROLE_DELEGATION_REJECTED')
    url = os.environ.get('ACTIONS_ID_TOKEN_REQUEST_URL', '')
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == 'https' and (parsed.hostname or '').endswith('.actions.githubusercontent.com'), 'OIDC_TOKEN_ENDPOINT_REJECTED')
    query = urllib.parse.parse_qsl(parsed.query)
    query = [(key, value) for key, value in query if key != 'audience'] + [('audience', 'sts.amazonaws.com')]
    url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
    with urllib.request.urlopen(request, timeout=30) as response:
        token = json.load(response)['value']
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config
    sts = boto3.client('sts', region_name=REGION, config=Config(signature_version=UNSIGNED))
    arn = f'arn:aws:iam::{ACCOUNT}:role/{PREFIX}-{role}'
    for attempt in range(6):
        try:
            credentials = sts.assume_role_with_web_identity(RoleArn=arn,
                RoleSessionName=f'complete-{os.environ["GITHUB_RUN_ID"]}-{role}', WebIdentityToken=token, DurationSeconds=3600)['Credentials']
            break
        except Exception as error:
            response = getattr(error, 'response', {})
            if not response.get('Error'):
                raise
            if attempt < 5 and response['Error']['Code'] in ('AccessDenied', 'AccessDeniedException', 'IDPCommunicationError'):
                time.sleep(10)
                continue
            raise Failure(response['Error']['Code'], arn, dict(aws_action='sts:AssumeRoleWithWebIdentity',
                original_reason=safe_diagnostic(response['Error'].get('Message', '')),
                correction='Confira a delegação immutable no plano IAM aprovado e o Environment real do job.')) from None
    os.environ.update(AWS_ACCESS_KEY_ID=credentials['AccessKeyId'], AWS_SECRET_ACCESS_KEY=credentials['SecretAccessKey'],
        AWS_SESSION_TOKEN=credentials['SessionToken'], EXECUTOR_ROLE=role)
    boto3.setup_default_session(aws_access_key_id=credentials['AccessKeyId'], aws_secret_access_key=credentials['SecretAccessKey'],
        aws_session_token=credentials['SessionToken'], region_name=REGION)


def deploy_all(config, catalog, report, artifact):
    require(os.environ.get('PROTECTED_ENVIRONMENT') == 'phase4-demo-a-bootstrap', 'ENVIRONMENT_APPROVAL_REQUIRED')
    pipeline.scope('deploy', 'iam-maintenance')
    pipeline.protection()
    reference, approved_hash = os.getenv('PLAN_RUN', ''), os.getenv('PLAN_HASH', '')
    read_bundle(config, catalog, reference, approved_hash, Path(artifact) / 'bundle.json')
    progress = report.directory / 'progress.json'
    for name, role, phases in BLOCKS:
        with report.phase('assume-oidc-role', role):
            assume(role)
        execute_block(config, catalog, report, name, reference, approved_hash, artifact, progress)


def destroy_all(config, catalog, report):
    pipeline.scope('destroy', 'infra')
    pipeline.protection()
    driver = None
    try:
        try:
            with report.phase('owned-kubernetes-teardown', PREFIX):
                assume('provision')
                report.data['operation'] = 'teardown'
                driver = driver_for(config, catalog, report, 'infra')
                driver.teardown()
        except BaseException:
            pass
        finally:
            if driver:
                driver.temporary.cleanup()
                driver = None
        with report.phase('owned-aws-destroy', PREFIX):
            assume('destroy')
            report.data['operation'] = 'destroy'
            driver = driver_for(config, catalog, report, 'infra')
            before = [dict(type=resource['type'], id=resource.get('id'), arn=resource.get('arn'))
                for resources in driver.journal['resources'].values() for resource in resources]
            driver.destroy()
            report.data['removed_resources'] = before
    finally:
        if driver:
            report.data['billable_resources'] = [dict(type=resource['type'], id=resource.get('id'), arn=resource.get('arn'))
                for resources in driver.journal['resources'].values() for resource in resources]
            report.data['preserved_resources'] = [config['backend_bucket'], PREFIX + '-artifacts',
                'OIDC roles/boundaries', 'independent endpoint recovery', '/oficina/phase4/demo-a/inputs/*',
                '/oficina/phase4/demo-a/auth-signing (persistent identity)']
            driver.temporary.cleanup()


def close(config, catalog, report):
    require(os.getenv('PROTECTED_ENVIRONMENT') in ('phase4-demo-a-bootstrap', 'phase4-demo-a-destroy'), 'ENVIRONMENT_APPROVAL_REQUIRED')
    if os.getenv('PROTECTED_ENVIRONMENT') == 'phase4-demo-a-destroy':
        require(os.getenv('CONFIRMATION') == 'DESTROY demo-a', 'CONFIRMATION_REJECTED')
    assume('provision')
    driver = pipeline.PipelineDriver(config, report, catalog=catalog)
    try:
        driver.load_closure()
        driver.close_endpoint()
    finally:
        driver.temporary.cleanup()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('plan-part', 'assemble', 'deploy-block', 'deploy-all', 'destroy-all', 'close'))
    parser.add_argument('--role', default=os.getenv('EXECUTOR_ROLE', 'provision'))
    parser.add_argument('--block', choices=[block[0] for block in BLOCKS])
    parser.add_argument('--parts', default='plan-parts')
    parser.add_argument('--artifact', default='approved-plan')
    parser.add_argument('--progress', default='previous-progress/progress.json')
    options = parser.parse_args()
    report = Report(options.operation)
    try:
        with report.phase('complete-execution', options.operation):
            config, catalog = pipeline.configuration()
            pipeline.scope('plan', 'infra')
            if options.operation not in ('destroy-all', 'close'):
                pipeline.artifact_key()
            if options.operation == 'plan-part':
                plan_part(config, catalog, report, options.role)
            elif options.operation == 'assemble':
                assemble(config, catalog, report, options.parts)
            elif options.operation == 'deploy-all':
                deploy_all(config, catalog, report, options.artifact)
            elif options.operation == 'destroy-all':
                destroy_all(config, catalog, report)
            elif options.operation == 'close':
                close(config, catalog, report)
            else:
                execute_block(config, catalog, report, options.block, os.getenv('PLAN_RUN', ''),
                    os.getenv('PLAN_HASH', ''), options.artifact, options.progress)
    except BaseException:
        pass
    finally:
        report.finish()
    return 1 if report.data['result'] in ('FAIL', 'BLOCKED') else 0


if __name__ == '__main__':
    sys.exit(main())
