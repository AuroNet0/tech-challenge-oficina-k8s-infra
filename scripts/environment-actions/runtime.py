import argparse
import base64
import contextlib
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
PREFIX = 'tc-oficina-p4-demo-a'
ACCOUNT = '446517826146'
REGION = 'us-east-1'
STACKS = ('infra', 'platform', 'edge')


class Failure(RuntimeError):
    def __init__(self, code, resource='', detail=None):
        super().__init__(code)
        self.code, self.resource, self.detail = code, resource, detail or {}


def require(condition, code, resource=''):
    if not condition:
        raise Failure(code, resource)


def code_of(error):
    return getattr(error, 'response', {}).get('Error', {}).get('Code', 'SDK_OPERATION_FAILED')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def cleanup_steps(report, actions, original_error=None):
    failure = None
    for resource, action in actions:
        try:
            with report.phase('cleanup', resource):
                action()
        except Exception as error:
            failure = failure or error
    if failure is not None and original_error is None:
        raise failure


def digest(value):
    return hashlib.sha256(value).hexdigest()


def module_at(name, directory, filename):
    sys.path.insert(0, str(ROOT / directory))
    spec = importlib.util.spec_from_file_location(name, ROOT / directory / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def safe_error(text):
    patterns = {
        'STATE_LOCK_FAILED': r'state lock|ConditionalCheckFailed',
        'ACCESS_DENIED': r'AccessDenied|UnauthorizedOperation|Forbidden',
        'AWS_CAPACITY_UNAVAILABLE': r'InsufficientInstanceCapacity|VcpuLimitExceeded',
        'RESOURCE_CONFLICT': r'AlreadyExists|ConflictException|EntityAlreadyExists',
        'INVALID_AWS_PARAMETER': r'InvalidParameter|ValidationException|InvalidRequest',
        'AWS_SERVICE_RESTRICTED': r'UnsupportedOperation|OptInRequired|SubscriptionRequired',
        'AWS_QUOTA_EXCEEDED': r'LimitExceeded|ServiceQuotaExceeded',
        'BACKEND_FAILED': r'backend|NoSuchBucket',
        'RESOURCE_MISSING': r'NotFound|NoSuchEntity|NoSuchKey',
        'READINESS_FAILED': r'CrashLoopBackOff|ProgressDeadlineExceeded|timed out waiting',
        'MIGRATION_FAILED': r'Flyway|PSQLException',
        'NETWORK_FAILED': r'connection refused|connection timed out|no such host',
    }
    return next((code for code, pattern in patterns.items() if re.search(pattern, text, re.I)), 'COMMAND_FAILED')


def safe_diagnostic(text):
    for name, value in os.environ.items():
        if re.search(r'secret|token|password|credential|private_key|access_key|authorization|artifact_key', name, re.I) and len(value) >= 4:
            text = text.replace(value, '[REDACTED]')
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)', '[REDACTED]', text)
    text = re.sub(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b', '[REDACTED]', text)
    text = re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', text)
    text = re.sub(r'(https?://[^\s?"\'<>]+)\?[^\s"\'<>]*', r'\1?[REDACTED]', text)
    text = re.sub(r'(?i)(authorization\s*[:=]\s*)(?:Bearer|Basic)\s+[^\s,;]+', r'\1[REDACTED]', text)
    text = re.sub(r'''(?ix)(["']?\b[\w.-]*(?:password|passwd|secret|token|credential|private_key|access_key|authorization|jwt)[\w.-]*["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)''', r'\1[REDACTED]', text)
    text = re.sub(r'^.*\b(?:\d+\s*:\s|[+~-]\s+[\w".-]+\s*=).*$', '[source values withheld]', text, flags=re.M)
    return text[:16384]


class Report:
    def __init__(self, operation, directory=None):
        self.directory = Path(directory or os.getenv('REPORT_DIR', 'actions-report'))
        self.directory.mkdir(parents=True, exist_ok=True)
        self.data = dict(operation=operation, environment='demo-a', run_id=os.getenv('GITHUB_RUN_ID'),
                         revision=os.getenv('GITHUB_SHA'), account=ACCOUNT, region=REGION,
                         checks=[], result='RUNNING', environment_ready=False)

    @contextlib.contextmanager
    def phase(self, phase, resource):
        started = time.monotonic()
        entry = dict(phase=phase, resource=resource, status='PASS', reason='VERIFIED')
        try:
            yield entry
        except BaseException as error:
            reason = error.code if isinstance(error, Failure) else code_of(error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                reason = 'EXECUTION_INTERRUPTED'
            if type(error).__name__ in ('ContractError', 'Rejected') and re.fullmatch(r'[A-Z_]{3,100}', str(error)):
                reason = str(error)
            entry.update(status='FAIL', reason=reason)
            if isinstance(error, Failure):
                entry['detail'] = error.detail
                entry['failed_resource'] = error.resource
                if error.resource:
                    entry['resource'] = error.resource
            else:
                entry['detail'] = dict(exception_type=type(error).__name__,
                    frames=[dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name)
                        for frame in traceback.extract_tb(error.__traceback__)[-8:]])
            corrections = {
                'ACCESS_DENIED': 'Confira a action IAM e o ARN no diagnóstico; atualize somente a policy/boundary da role executora.',
                'AccessDenied': 'Confira a action IAM e o ARN no diagnóstico; atualize somente a policy/boundary da role executora.',
                'STATE_LOCK_FAILED': 'Identifique o proprietário do lock S3 e aguarde sua execução; não remova um lock ativo.',
                'AWS_QUOTA_EXCEEDED': 'Confira a quota indicada em us-east-1; reduza o escopo ou solicite ajuste sem mudar o plano da conta.',
                'AWS_CAPACITY_UNAVAILABLE': 'Confira capacidade nas zonas e tipos aprovados; alterações de capacidade exigem novo Plan.',
                'READINESS_FAILED': 'Confira eventos, PVCs, logs atuais/anteriores e conexão com os bancos e RabbitMQ.',
                'MIGRATION_FAILED': 'Confira o Job, versão Flyway e grants do banco indicado; preserve o state e o banco.',
                'AWS_E2E_TRANSITION_TIMEOUT': 'Confira a última transição da Saga e o checkout de teste; não simule confirmação financeira.',
            }
            entry['message_pt'] = 'Falha na etapa ' + phase + ': ' + reason
            entry['next_action'] = corrections.get(reason, 'Confira o recurso e a causa original nesta etapa; corrija o requisito e retome. State e inventário são preservados.')
            raise
        finally:
            entry['duration_ms'] = round((time.monotonic() - started) * 1000)
            self.data['checks'].append(entry)
            self.write()

    def write(self):
        statuses = {item['status'] for item in self.data['checks']}
        self.data['result'] = 'FAIL' if 'FAIL' in statuses else 'BLOCKED' if 'BLOCKED' in statuses else 'PASS'
        (self.directory / 'report.json').write_text(json.dumps(self.data, indent=2), encoding='utf-8')
        rows = ['# Environment executor: ' + self.data['result'],
                f"Run {self.data['run_id']}; revision {self.data['revision']}", '',
                '| Phase | Resource | Status | Reason | ms |', '|---|---|---|---|---:|']
        rows += [f"| {entry['phase']} | {entry['resource']} | {entry['status']} | {entry['reason']} | {entry['duration_ms']} |"
                 for entry in self.data['checks']]
        rows += ['', f"Account: {ACCOUNT}; region: {REGION}; operation: {self.data['operation']}; phase: {self.data.get('phase', 'n/a')}",
                 f"Terraform: {self.data.get('terraform_result', 'See checks')}"]
        if self.data.get('plan_sha256'):
            rows += ['', 'Approved plan SHA256: `' + self.data['plan_sha256'] + '`']
        for entry in self.data['checks']:
            if entry['status'] == 'FAIL':
                rows += ['', 'Failed step: ' + entry['phase'], 'Action: ' + entry.get('next_action', ''),
                         'Diagnostic: `' + json.dumps(entry.get('detail', {}), sort_keys=True) + '`']
        (self.directory / 'summary.md').write_text('\n'.join(rows) + '\n', encoding='utf-8')

    def finish(self):
        self.write()
        if os.getenv('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as summary:
                summary.write((self.directory / 'summary.md').read_text(encoding='utf-8'))


def current_review(configuration, scope):
    review = configuration.get('preflight_authorization', {})
    return (review.get('account') == ACCOUNT and review.get('region') == REGION and review.get('scope') == scope
            and isinstance(review.get('valid_until'), (int, float))
            and time.time() < review['valid_until'] < time.time() + 86400
            and isinstance(review.get('reference'), str) and bool(review['reference'].strip()))


def guard(operation, configuration, catalog, environment=None, execution_mode='apply', phase='infra',
          execution_configuration=None, cleanup=False):
    environment = os.environ if environment is None else environment
    require(environment.get('GITHUB_REPOSITORY') == 'AuroNet0/tech-challenge-oficina-k8s-infra'
            and environment.get('GITHUB_REF') == 'refs/heads/homolog'
            and environment.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', 'DISPATCH_SCOPE_REJECTED')
    require(execution_mode in ('plan', 'apply'), 'EXECUTION_MODE_REJECTED')
    planning = operation == 'up' and execution_mode == 'plan' and not cleanup
    require(environment.get('PHASE4_PLAN_APPROVED' if planning else 'PHASE4_EXECUTION_APPROVED') == 'true',
            'PLAN_NOT_AUTHORIZED' if planning else 'EXECUTION_NOT_AUTHORIZED')
    require(environment.get('CONFIRMATION') == operation.upper() + ' demo-a', 'CONFIRMATION_REJECTED')
    require(configuration.get('account') == ACCOUNT and configuration.get('region') == REGION
            and configuration.get('environment') == 'demo-a', 'CONFIGURATION_SCOPE_REJECTED')
    require(re.fullmatch(r'[0-9a-f]{40}', environment.get('GITHUB_SHA', '')), 'REVISION_REQUIRED')
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', configuration.get('backend_bucket', '')), 'BACKEND_NOT_APPROVED')
    require(catalog.get('backend_bucket') == configuration['backend_bucket'] and catalog.get('aws_account_id') == ACCOUNT,
            'CATALOG_BACKEND_MISMATCH')
    if planning:
        require(phase == 'infra', 'PLAN_SCOPE_REJECTED')
        require(catalog.get('aws_region') == REGION, 'CATALOG_REGION_MISMATCH')
        require(current_review(configuration, 'infra')
                and configuration['preflight_authorization'].get('backend_permissions_reviewed') is True,
                'PLANNING_BACKEND_NOT_REVIEWED')
        return
    require(configuration.get('approved') is True, 'BACKEND_NOT_APPROVED')
    if operation == 'up':
        require(catalog.get('enabled') is True, 'ENVIRONMENT_DISABLED')
        require(configuration.get('destroy_offline_verified') is True, 'DESTROY_VALIDATION_REQUIRED')
        if not cleanup:
            if execution_configuration is not None:
                require(execution_configuration.get('execution_enabled') is True, 'EXECUTION_DISABLED')
            scope = 'infra' if phase == 'infra' else 'full'
            review = configuration.get('preflight_authorization', {})
            services = {'eks', 'ec2', 'rds', 'ecr', 'secretsmanager', 'logs', 'iam'}
            if scope == 'full':
                services.update({'lambda', 'elbv2'})
            require(current_review(configuration, scope) and review.get('free_plan_creation_reviewed') is True
                    and services.issubset(set(review.get('free_services_reviewed', []))), 'CREATION_ELIGIBILITY_NOT_REVIEWED')
            require(review.get('backend_permissions_reviewed') is True, 'BACKEND_WRITE_PERMISSIONS_NOT_PROVEN')
            endpoint = configuration.get('endpoint', {})
            require(endpoint.get('mode') in ('broker', 'existing-private')
                    and endpoint.get('private_creation_reviewed') is True and endpoint.get('teardown_reviewed') is True
                    and isinstance(endpoint.get('recovery_reference'), str) and bool(endpoint['recovery_reference'].strip()),
                    'EKS_RECOVERY_STRATEGY_NOT_REVIEWED')


class Driver:
    def __init__(self, configuration, report, clients=None, execute=None, sleep=time.sleep, catalog=None,
                 execution_configuration=None):
        self.config, self.report, self.sleep = configuration, report, sleep
        self.clients = clients or {}
        self.execute = execute or subprocess.run
        self.bucket = configuration['backend_bucket']
        self.key = 'phase4/environments/demo-a/operations/inventory.json'
        self.journal = dict(schema_version=1, account=ACCOUNT, region=REGION, environment='demo-a', stacks={},
                            resources={}, secrets={}, images={}, objects=[], layers=[], receipts={})
        self.lease = None
        self.catalog = catalog if catalog is not None else json.loads((ROOT / 'phase4/environments/catalog.json').read_text())['environments']['demo-a']
        self.execution_configuration = execution_configuration if execution_configuration is not None else json.loads((ROOT / 'phase4/environments/actions-first.json').read_text())
        self.temporary = tempfile.TemporaryDirectory(prefix='phase4-actions-')

    def client(self, service):
        if service not in self.clients:
            import boto3
            from botocore.config import Config
            self.clients[service] = boto3.client(service, region_name=REGION,
                config=Config(connect_timeout=5, read_timeout=30, retries={'mode': 'standard', 'total_max_attempts': 3}))
        return self.clients[service]

    def api(self, service, operation, **arguments):
        try:
            return getattr(self.client(service), operation)(**arguments)
        except Exception as error:
            metadata = getattr(error, 'response', {}).get('ResponseMetadata', {})
            resource = next((str(arguments[key]) for key in ('ResourceArn', 'RoleName', 'Bucket', 'SecretId', 'name', 'Key') if key in arguments), service)
            raise Failure(code_of(error), f'{service}:{operation}', dict(request_id=metadata.get('RequestId'),
                http_status=metadata.get('HTTPStatusCode'), aws_action=service + ':' + operation,
                resource=resource, original_reason=safe_diagnostic(getattr(error, 'response', {}).get('Error', {}).get('Message', '')))) from None

    def items(self, service, operation, result, **arguments):
        client = self.client(service)
        try:
            if client.can_paginate(operation):
                return [item for page in client.get_paginator(operation).paginate(**arguments) for item in page.get(result, [])]
            return getattr(client, operation)(**arguments).get(result, [])
        except Exception as error:
            raise Failure(code_of(error), f'{service}:{operation}') from None

    def run(self, arguments, payload=None, timeout=900, directory=None):
        try:
            result = self.execute(arguments, input=payload, cwd=directory, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Failure('COMMAND_TIMEOUT', arguments[0]) from None
        except FileNotFoundError:
            raise Failure('EXECUTABLE_UNAVAILABLE', arguments[0]) from None
        stdout = result.stdout.decode(errors='replace') if isinstance(result.stdout, bytes) else result.stdout or ''
        stderr = result.stderr.decode(errors='replace') if isinstance(result.stderr, bytes) else result.stderr or ''
        if result.returncode:
            context = dict(exit_code=result.returncode, operation=arguments[0])
            if arguments[0] == 'terraform':
                diagnostics = []
                records = []
                try:
                    document = json.loads(stdout)
                    if isinstance(document, dict):
                        records.extend(document.get('diagnostics', []))
                except ValueError:
                    pass
                for line in stdout.splitlines():
                    try:
                        diagnostic = json.loads(line).get('diagnostic', {})
                    except ValueError:
                        continue
                    if diagnostic:
                        records.append(diagnostic)
                if not records:
                    records.append(dict(summary='Terraform command failed', detail=stdout + stderr, raw_output_withheld=True))
                for diagnostic in records:
                    address = diagnostic.get('address')
                    if diagnostic and (not address or re.fullmatch(r'[a-zA-Z0-9_.\[\]"/-]+', address)):
                        text = diagnostic.get('summary', '') + diagnostic.get('detail', '')
                        operation = re.search(r'operation error ([A-Za-z0-9]+): ([A-Za-z0-9]+)', text)
                        aws_code = re.search(r'\b(AccessDenied(?:Exception)?|UnauthorizedOperation|InvalidParameter\w*|NoSuch\w+|\w*NotFound\w*|\w*LimitExceeded\w*)\b', text)
                        denied_action = re.search(r'(?:perform:|action:)\s*([A-Za-z0-9-]+:[A-Za-z0-9]+)', text)
                        affected = re.search(r'arn:aws:[a-z0-9-]+:[a-z0-9-]*:[0-9]*:[A-Za-z0-9_./:-]+', text)
                        diagnostics.append(dict(resource=address, cause=safe_error(text),
                            summary=safe_diagnostic(diagnostic.get('summary', '')),
                            detail='' if diagnostic.get('raw_output_withheld') else safe_diagnostic(diagnostic.get('detail', '')),
                            provider_operation=operation.group(0) if operation else None,
                            aws_code=aws_code.group(0) if aws_code else None,
                            required_action=denied_action.group(1) if denied_action else None,
                            affected_resource=affected.group(0) if affected else None))
                context['diagnostics'] = diagnostics
            raise Failure(safe_error(stdout + stderr), arguments[0], context)
        return stdout

    def load(self):
        identity = self.api('sts', 'get_caller_identity')
        expected_role = os.environ['EXECUTOR_ROLE']
        require(identity['Account'] == ACCOUNT and identity['Arn'].startswith(
            f'arn:aws:sts::{ACCOUNT}:assumed-role/{PREFIX}-{expected_role}/'), 'OIDC_IDENTITY_REJECTED')
        require(self.api('s3', 'get_bucket_versioning', Bucket=self.bucket, ExpectedBucketOwner=ACCOUNT).get('Status') == 'Enabled', 'BACKEND_VERSIONING_REQUIRED')
        try:
            response = self.api('s3', 'get_object', Bucket=self.bucket, Key=self.key, ExpectedBucketOwner=ACCOUNT)
        except Failure as error:
            if error.code != 'NoSuchKey':
                raise
        else:
            self.journal = json.loads(response['Body'].read())
            require(all(self.journal.get(key) == value for key, value in dict(account=ACCOUNT, region=REGION, environment='demo-a', schema_version=1).items()), 'INVENTORY_SCOPE_REJECTED')

    def save(self):
        self.api('s3', 'put_object', Bucket=self.bucket, Key=self.key, Body=canonical(self.journal),
                 ExpectedBucketOwner=ACCOUNT, ServerSideEncryption='AES256', ContentType='application/json')
        (self.report.directory / 'identity-inventory.json').write_bytes(canonical(dict(inventory=self.identity_inventory(), inventory_approved=False)))

    def identity_inventory(self):
        inventory = {key: [] for key in ['ec2', 'eks_children', 'api_resources', 'rds', 'ecr', 'lambda', 'log_groups', 'workload_roles']}
        inventory.update(secrets=self.journal['secrets'], workload_boundaries={})
        for resources in self.journal['resources'].values():
            for resource in resources:
                arn = resource.get('arn')
                if not arn:
                    continue
                family = resource['type']
                key = next((key for token, key in [('aws_vpc', 'ec2'), ('aws_subnet', 'ec2'), ('aws_security_group', 'ec2'),
                    ('aws_route_table', 'ec2'), ('aws_internet_gateway', 'ec2'), ('aws_launch_template', 'ec2'),
                    ('aws_eks_', 'eks_children'), ('aws_db_', 'rds'), ('aws_ecr_repository', 'ecr'),
                    ('aws_lambda_', 'lambda'), ('aws_cloudwatch_log_group', 'log_groups'), ('aws_iam_role', 'workload_roles'),
                    ('aws_apigatewayv2_', 'api_resources')] if family.startswith(token)), None)
                if key:
                    inventory[key].append(arn)
        inventory['lambda'] = sorted(set(inventory['lambda'] + self.journal['layers']))
        for values in self.config.get('terraform_inputs', {}).values():
            inventory['workload_boundaries'].update(values.get('workload_permissions_boundaries', {}))
        if self.journal.get('rds_master_secret_arn'):
            inventory['rds_master_secret_arn'] = self.journal['rds_master_secret_arn']
        return inventory

    def receipt(self, name, value):
        self.journal['receipts'][name] = dict(revision=os.environ['GITHUB_SHA'], observed_at=int(time.time()), **value)
        self.save()

    def tf(self, stack, arguments):
        require(stack in STACKS, 'STACK_REJECTED')
        return self.run(['terraform', f'-chdir={ROOT / "phase4/stacks" / stack}', *arguments], timeout=3300)

    def init(self, stack):
        os.environ['TF_WORKSPACE'] = 'default'
        self.tf(stack, ['init', '-input=false', '-reconfigure', '-lockfile=readonly',
            f'-backend-config=bucket={self.bucket}', '-backend-config=region=us-east-1',
            f'-backend-config=key=phase4/environments/demo-a/{stack}.tfstate',
            '-backend-config=workspace_key_prefix=phase4/environments/demo-a/workspaces',
            '-backend-config=encrypt=true', '-backend-config=use_lockfile=true'])

    def outputs(self, stack):
        self.init(stack)
        return {key: value['value'] for key, value in json.loads(self.tf(stack, ['output', '-json'])).items()}

    def snapshot(self, stack, persist=True):
        document = json.loads(self.tf(stack, ['show', '-json']))
        resources = []

        def visit(module):
            for resource in module.get('resources', []):
                if resource.get('mode') != 'managed':
                    continue
                values = resource.get('values', {})
                tags = values.get('tags_all') or values.get('tags') or {}
                require(tags.get('EnvironmentId', 'demo-a') == 'demo-a', 'FOREIGN_STATE_RESOURCE', resource['address'])
                arn = values.get('arn') or values.get('access_entry_arn') or values.get('association_arn')
                if resource['type'].startswith('aws_apigatewayv2_') and not arn:
                    suffix = resource['type'].removeprefix('aws_apigatewayv2_')
                    if suffix == 'api':
                        arn = f'arn:aws:apigateway:{REGION}::/apis/{values["id"]}'
                    elif suffix == 'vpc_link':
                        arn = f'arn:aws:apigateway:{REGION}::/vpclinks/{values["id"]}'
                    elif suffix in ('route', 'stage', 'integration', 'authorizer'):
                        identifier = values.get('name') if suffix == 'stage' else values['id']
                        arn = f'arn:aws:apigateway:{REGION}::/apis/{values["api_id"]}/{suffix}s/{identifier}'
                if arn and arn.startswith('arn:aws:'):
                    parts = arn.split(':', 5)
                    require(parts[4] in ('', ACCOUNT) and parts[3] in ('', REGION), 'FOREIGN_STATE_RESOURCE', resource['address'])
                if resource['type'] == 'aws_db_instance' and values.get('master_user_secret'):
                    self.journal['rds_master_secret_arn'] = values['master_user_secret'][0]['secret_arn']
                if resource['type'] == 'aws_vpc':
                    self.journal['vpc_id'] = values['id']
                resources.append(dict(address=resource['address'], type=resource['type'],
                    id=values.get('id'), arn=arn, tags={key: value for key, value in tags.items() if key in ('EnvironmentId', 'Phase', 'Lifecycle', 'Shared')}))
            for child in module.get('child_modules', []):
                visit(child)

        visit(document.get('values', {}).get('root_module', {}))
        self.journal['resources'][stack] = resources
        if persist:
            self.save()
        return resources

    def variables(self, stack, profile=None):
        values = dict(self.config['terraform_inputs'].get(stack, {}), environment_id='demo-a',
                      aws_account_id=ACCOUNT, aws_region=REGION, git_revision=os.environ['GITHUB_SHA'])
        require(not any(re.search(r'password|private_key|secret_string|access_token', key, re.I) for key in values), 'SENSITIVE_TERRAFORM_INPUT_REJECTED')
        if stack == 'infra':
            values.update(node_count=2, eks_public_access_cidrs=[])
        else:
            infra = self.outputs('infra')['platform_contract']
            values['infra_contract'] = infra
            if stack == 'platform':
                require(set(self.journal['images']) == {'auth', 'os', 'execution', 'billing'}, 'PUBLISHED_IMAGES_REQUIRED')
                values['images'] = dict(values.get('images', {}), **{key: value['reference'] for key, value in self.journal['images'].items()})
                edge = self.outputs('edge')
                if edge.get('jwt_contract'):
                    values['jwt'] = edge['jwt_contract']
                    payment = values.get('mercado_pago', {})
                    if all(payment.get(name + '_url') for name in ('success', 'failure', 'pending')):
                        values['mercado_pago'] = dict(payment, enabled=True,
                            notification_url=edge['jwt_contract']['issuer'] + '/api/v1/webhooks/mercado-pago')
                receipts = self.journal['receipts']
                for flag, receipt in dict(bootstrap_completed='bootstrap', secrets_delivered='secrets',
                        auth_image_verified='release', auth_migration_completed='databases',
                        network_policy_enforced='network-policy', auth_internal_runtime_approved='release').items():
                    values[flag] = receipts.get(receipt, {}).get('verified') is True
                values['application_replicas'] = 1 if profile == 'platform-apps' else 0
                values['auth_migration_enabled'] = profile in ('platform-migration', 'platform-apps')
                tls = self.config['terraform_inputs'].get('edge', {}).get('alb_tls')
                if edge.get('edge_contract') and tls:
                    values['alb_controller_identity_enabled'] = True
                    values['alb_identity_inputs'] = dict(security_group_id=edge['edge_contract']['alb_security_group_id'], certificate_arn=tls['certificate_arn'])
            else:
                values['platform_contract'] = self.outputs('platform')['edge_contract']
                if self.journal.get('auth_release'):
                    values['auth_release'] = self.journal['auth_release']
                receipts = self.journal['receipts']
                values.update(publish_auth=profile in ('edge-auth', 'edge-alb', 'edge-business'),
                    prepare_alb=profile in ('edge-alb', 'edge-business'), publish_business=profile == 'edge-business',
                    publish_webhook=profile == 'edge-business')
                mapping = dict(artifact_verified='release', bootstrap_layer_verified='release', secrets_verified='secrets',
                    bootstrap_completed='bootstrap', auth_migration_completed='databases', controller_ready='controller',
                    workloads_ready='health', network_policy_enforced='network-policy', pki_valid_for_operation='secrets',
                    jwks_ready='jwks', targets_healthy='alb', listener_verified='alb', mercado_pago_verified='mercado-pago')
                values['readiness'] = {flag: receipts.get(receipt, {}).get('verified') is True for flag, receipt in mapping.items()}
                if receipts.get('alb', {}).get('listener_arn'):
                    values['alb_listener_arn'] = receipts['alb']['listener_arn']
        self.verify_attestations(stack, values)
        return values

    def verify_attestations(self, stack, values):
        receipts = self.journal['receipts']
        required_receipts = set()
        needed = {'bootstrap_completed': 'bootstrap', 'secrets_delivered': 'secrets',
                  'auth_migration_completed': 'databases', 'auth_image_verified': 'release',
                  'network_policy_enforced': 'network-policy', 'auth_internal_runtime_approved': 'release'}
        for flag, receipt in needed.items():
            if values.get(flag):
                require(receipts.get(receipt, {}).get('verified') is True, 'LIVE_EVIDENCE_REQUIRED', receipt)
                required_receipts.add(receipt)
        if stack == 'edge':
            mapping = dict(artifact_verified='release', bootstrap_layer_verified='release', secrets_verified='secrets',
                bootstrap_completed='bootstrap', auth_migration_completed='databases', controller_ready='controller',
                workloads_ready='health', network_policy_enforced='network-policy', pki_valid_for_operation='secrets',
                jwks_ready='jwks', targets_healthy='alb', listener_verified='alb', mercado_pago_verified='mercado-pago')
            for flag, enabled in values.get('readiness', {}).items():
                require(flag in mapping and (not enabled or receipts.get(mapping[flag], {}).get('verified') is True), 'LIVE_EVIDENCE_REQUIRED', flag)
                if enabled:
                    required_receipts.add(mapping[flag])
        for name in required_receipts:
            receipt = receipts[name]
            require(receipt.get('revision') == os.environ['GITHUB_SHA'] and 0 <= time.time() - receipt.get('observed_at', 0) < 7200, 'EVIDENCE_STALE', name)

    def plan_apply(self, stack, approved_hash='', profile=None, execution_mode='plan'):
        require(execution_mode in ('plan', 'apply'), 'EXECUTION_MODE_REJECTED')
        if execution_mode == 'plan':
            require(not approved_hash, 'PLAN_HASH_NOT_ALLOWED')
        else:
            require(re.fullmatch(r'[0-9a-f]{64}', approved_hash), 'APPROVED_PLAN_HASH_REQUIRED')
        guard('up', self.config, self.catalog, execution_mode=execution_mode, phase=profile or stack,
              execution_configuration=self.execution_configuration)
        self.init(stack)
        values = self.variables(stack, profile)
        require(execution_mode != 'apply' or not values.get('planning_only'), 'PLANNING_INPUT_CANNOT_APPLY')
        if stack == 'infra':
            values['planning_only'] = execution_mode == 'plan' and self.catalog.get('enabled') is not True
        inputs_file = Path(self.temporary.name) / (stack + '.json')
        inputs_file.write_bytes(canonical(values))
        plan_file = Path(self.temporary.name) / (stack + '.tfplan')
        self.tf(stack, ['fmt', '-check'])
        self.tf(stack, ['validate', '-json'])
        self.tf(stack, ['plan', '-json', '-input=false', '-lock-timeout=60s', f'-var-file={inputs_file}', f'-out={plan_file}'])
        document = json.loads(self.tf(stack, ['show', '-json', str(plan_file)]))
        require(not document.get('errored') and not document.get('deferred_changes'), 'PLAN_INCOMPLETE')
        changes = [change for change in document.get('resource_changes', []) if change.get('mode') != 'data']
        require(not any('delete' in change['change']['actions'] for change in changes), 'UP_REPLACEMENT_REQUIRES_SEPARATE_REVIEW')
        fingerprint = digest(canonical(dict(revision=os.environ['GITHUB_SHA'], stack=stack, variables=values,
            changes=changes, outputs=document.get('output_changes', {}))))
        summary = dict(stack=stack, sha256=fingerprint, execution_mode=execution_mode,
            requires_enabled_replan=bool(values.get('planning_only', False)),
            changes=[dict(address=change['address'], actions=change['change']['actions']) for change in changes])
        (self.report.directory / 'plan.json').write_bytes(canonical(summary))
        if execution_mode == 'plan':
            return 'PLAN_ONLY'
        require(re.fullmatch(r'[0-9a-f]{64}', approved_hash) and approved_hash == fingerprint, 'APPROVED_PLAN_HASH_MISMATCH')
        guard('up', self.config, self.catalog, execution_mode='apply', phase=profile or stack,
              execution_configuration=self.execution_configuration)
        self.journal['stacks'][stack] = values
        self.save()
        try:
            self.tf(stack, ['apply', '-json', '-input=false', '-lock-timeout=60s', str(plan_file)])
        except BaseException:
            try:
                with self.report.phase('checkpoint-after-failed-apply', stack):
                    self.snapshot(stack)
            except Exception:
                pass
            raise
        self.snapshot(stack)
        return 'APPLIED'

    def cluster(self, missing_ok=False):
        try:
            cluster = self.api('eks', 'describe_cluster', name=PREFIX)['cluster']
        except Failure as error:
            if missing_ok and error.code == 'ResourceNotFoundException':
                return None
            raise
        require(cluster['arn'] == f'arn:aws:eks:{REGION}:{ACCOUNT}:cluster/{PREFIX}' and cluster.get('tags', {}).get('EnvironmentId') == 'demo-a', 'CLUSTER_OWNERSHIP_REJECTED')
        return cluster

    def wait(self, predicate, code, attempts=60, delay=10):
        for attempt in range(attempts):
            if predicate():
                return
            self.sleep(delay)
        raise Failure(code)

    def invoke(self, purpose, payload):
        function = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{PREFIX}-endpoint-{purpose}'
        result = self.api('lambda', 'invoke', FunctionName=function, InvocationType='RequestResponse', Payload=canonical(payload))
        body = json.loads(result['Payload'].read())
        if result.get('FunctionError'):
            raise Failure('ENDPOINT_RECOVERY_REJECTED', function,
                          dict(error_type=safe_diagnostic(str(body.get('errorType', 'LambdaError'))),
                               message=safe_diagnostic(str(body.get('errorMessage', 'Endpoint operation failed')))))
        return body

    def recovery_ready(self, opening=True):
        from package_recovery import package_bytes
        checksum = base64.b64encode(bytes.fromhex(digest(package_bytes()))).decode()
        for purpose in ('broker', 'recovery'):
            function = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{PREFIX}-endpoint-{purpose}'
            observed = self.api('lambda', 'get_function_configuration', FunctionName=function)
            settings = json.loads(observed.get('Environment', {}).get('Variables', {}).get('RECOVERY_CONFIG', '{}'))
            require(observed.get('State') == 'Active' and observed.get('LastUpdateStatus') == 'Successful'
                    and observed.get('Role') == f'arn:aws:iam::{ACCOUNT}:role/{PREFIX}-endpoint-{purpose}'
                    and observed.get('CodeSha256') == settings.get('code_sha256') == checksum
                    and settings.get('cluster_arn') == f'arn:aws:eks:{REGION}:{ACCOUNT}:cluster/{PREFIX}'
                    and settings.get('bootstrap_principal_arn') == f'arn:aws:iam::{ACCOUNT}:role/{PREFIX}-provision'
                    and settings.get('account') == ACCOUNT and settings.get('region') == REGION
                    and settings.get('environment_id') == 'demo-a', 'RECOVERY_INSTALLATION_NOT_READY', function)
            if opening:
                require(settings.get('enabled') is True and settings.get('recovery_verified') is True,
                        'BROKER_OPENING_DISABLED', function)
        schedule = self.api('scheduler', 'get_schedule', Name='reconcile', GroupName=PREFIX + '-recovery')
        require(schedule.get('State') == 'ENABLED'
                and schedule.get('Target', {}).get('Arn') == f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{PREFIX}-endpoint-recovery',
                'INDEPENDENT_RECOVERY_SCHEDULE_REQUIRED')

    def bootstrap_permissions(self):
        checks = [('create', 'customresourcedefinitions.apiextensions.k8s.io'),
                  ('create', 'clusterroles.rbac.authorization.k8s.io'),
                  ('bind', 'clusterroles.rbac.authorization.k8s.io'),
                  ('escalate', 'clusterroles.rbac.authorization.k8s.io'),
                  ('create', 'clusterrolebindings.rbac.authorization.k8s.io')]
        for verb, resource in checks:
            allowed = self.kubectl(['auth', 'can-i', verb, resource]).strip()
            require(allowed == 'yes', 'KUBERNETES_BOOTSTRAP_FORBIDDEN', verb + ':' + resource)

    @contextlib.contextmanager
    def kubernetes(self, privileged=False):
        cluster = self.cluster()
        require(cluster['status'] == 'ACTIVE', 'CLUSTER_NOT_ACTIVE')
        if self.config.get('endpoint', {}).get('mode') == 'existing-private':
            require(not privileged, 'BOOTSTRAP_REQUIRES_DURABLE_BROKER')
            require(os.getenv('RUNNER_ENVIRONMENT') == 'self-hosted' and not cluster['resourcesVpcConfig']['endpointPublicAccess']
                and cluster['resourcesVpcConfig']['endpointPrivateAccess'], 'PRIVATE_RUNNER_NOT_PROVEN')
            os.environ['KUBECONFIG'] = str(Path(self.temporary.name) / 'kubeconfig')
            self.run(['aws', 'eks', 'update-kubeconfig', '--region', REGION, '--name', PREFIX])
            self.run(['kubectl', 'get', '--raw=/readyz', '--request-timeout=15s'])
            try:
                yield
            except BaseException:
                try:
                    self.diagnostics()
                except Exception:
                    pass
                raise
            return
        require(self.config.get('endpoint', {}).get('mode') == 'broker', 'BROKER_CONFIGURATION_REQUIRED')
        require(self.config['endpoint'].get('independent_canary_verified') is True, 'INDEPENDENT_RECOVERY_CANARY_REQUIRED')
        require(not cluster['resourcesVpcConfig']['endpointPublicAccess'], 'UNEXPECTED_PUBLIC_ENDPOINT')
        with urllib.request.urlopen('https://checkip.amazonaws.com', timeout=10) as response:
            observed_ip = response.read(64).decode().strip()
        address = ipaddress.ip_address(observed_ip)
        require(address.version == 4 and address.is_global, 'RUNNER_IP_REJECTED')
        lease = self.invoke('broker', {'prepare': dict(github_run_id=os.environ['GITHUB_RUN_ID'], revision=os.environ['GITHUB_SHA'],
            github_run_attempt=os.getenv('GITHUB_RUN_ATTEMPT', '1'), observed_ip=observed_ip, duration_seconds=1800)})
        self.lease = lease['lease_id']
        original_error = None
        try:
            (self.report.directory / 'endpoint-lease.json').write_bytes(canonical(dict(lease_id=self.lease,
                run_id=os.environ['GITHUB_RUN_ID'], revision=os.environ['GITHUB_SHA'])))
            self.receipt('endpoint-lease', dict(lease_id=self.lease, expires_at=lease['expires_at']))
            self.invoke('broker', {'open': self.lease})
            self.wait(lambda: self.cluster()['status'] == 'ACTIVE' and self.cluster()['resourcesVpcConfig']['endpointPublicAccess'], 'ENDPOINT_OPEN_TIMEOUT', attempts=40)
            network = self.cluster()['resourcesVpcConfig']
            require(network['endpointPrivateAccess'] and network['publicAccessCidrs'] == [observed_ip + '/32'], 'ENDPOINT_CIDR_MISMATCH')
            os.environ['KUBECONFIG'] = str(Path(self.temporary.name) / 'kubeconfig')
            self.run(['aws', 'eks', 'update-kubeconfig', '--region', REGION, '--name', PREFIX])
            if privileged:
                require(getattr(self, 'authorization_source', None) == 'github-environment'
                        and os.getenv('EXECUTOR_ROLE') == 'provision', 'BOOTSTRAP_NOT_AUTHORIZED')
                with self.report.phase('temporary-kubernetes-bootstrap', PREFIX):
                    result = self.invoke('broker', {'bootstrap': self.lease})
                    require(result.get('bootstrap_elevation') is True and result.get('lease_id') == self.lease,
                            'BOOTSTRAP_POLICY_NOT_ASSOCIATED')
                    self.wait(lambda: self.kubernetes_authorized(), 'KUBERNETES_BOOTSTRAP_FORBIDDEN', attempts=12, delay=5)
            self.run(['kubectl', 'get', '--raw=/readyz', '--request-timeout=15s'])
            yield
        except BaseException as error:
            original_error = error
            try:
                self.diagnostics()
            except Exception:
                pass
            raise
        finally:
            try:
                with self.report.phase('endpoint-close-independent-verification', PREFIX):
                    self.close_endpoint()
            except Exception:
                if original_error is None:
                    raise

    def kubernetes_authorized(self):
        try:
            self.bootstrap_permissions()
            return True
        except Failure as error:
            if error.code not in ('KUBERNETES_BOOTSTRAP_FORBIDDEN', 'COMMAND_FAILED', 'ACCESS_DENIED'):
                raise
            return False

    def close_endpoint(self):
        lease_id = self.lease or self.journal['receipts'].get('endpoint-lease', {}).get('lease_id')
        if not lease_id:
            cluster = self.cluster(missing_ok=True)
            require(cluster is None or (not cluster['resourcesVpcConfig']['endpointPublicAccess']
                and cluster['resourcesVpcConfig']['endpointPrivateAccess']), 'PUBLIC_ENDPOINT_WITHOUT_LEASE')
            return
        for attempt in range(12):
            try:
                result = self.invoke('recovery', {'lease_id': lease_id})
                if result.get('state') in ('CLOSED', 'MISSING'):
                    cluster = self.cluster(missing_ok=True)
                    require(cluster is None or (not cluster['resourcesVpcConfig']['endpointPublicAccess']
                        and cluster['resourcesVpcConfig']['endpointPrivateAccess']), 'INDEPENDENT_CLOSE_NOT_CONFIRMED')
                    return
            except Failure as error:
                if error.code != 'ENDPOINT_RECOVERY_REJECTED':
                    raise
            self.sleep(10)
        raise Failure('INDEPENDENT_CLOSE_NOT_CONFIRMED')

    def kubectl(self, arguments, document=None):
        if self.lease:
            require(time.time() < self.journal['receipts']['endpoint-lease']['expires_at'] - 30, 'ENDPOINT_LEASE_EXPIRED')
        return self.run(['kubectl', *arguments, '--request-timeout=30s'], canonical(document) if document is not None else None)

    def apply_bundle(self, bundle):
        document = json.loads(bundle) if isinstance(bundle, str) else bundle
        require(document.get('kind') == 'List', 'MANIFEST_LIST_REQUIRED')
        for item in document['items']:
            require(item.get('kind') != 'Secret', 'SECRET_IN_MANIFEST_REJECTED')
            namespace = item['metadata'].get('namespace')
            require(namespace in (None, PREFIX, PREFIX + '-observability'), 'NAMESPACE_REJECTED')
            if item['kind'] == 'Job':
                existing = json.loads(self.kubectl(['get', 'jobs', '-n', PREFIX, '-o', 'json']))['items']
                current = next((job for job in existing if job['metadata']['name'] == item['metadata']['name']), None)
                if current:
                    require(current['spec']['template']['spec']['containers'][0]['image'] == item['spec']['template']['spec']['containers'][0]['image'], 'JOB_IMAGE_MISMATCH')
                    if current.get('status', {}).get('failed'):
                        require(os.getenv('RETRY_FAILED_JOBS') == 'true' and current['metadata'].get('labels', {}).get('oficina/environment') == 'demo-a', 'FAILED_JOB_REQUIRES_EXPLICIT_RETRY')
                        self.diagnostics()
                        self.kubectl(['delete', 'job', item['metadata']['name'], '-n', PREFIX, '--wait=true'])
                    else:
                        continue
            self.kubectl(['apply', '-f', '-'], item)

    def verify_job(self, manifest):
        for job in json.loads(manifest)['items']:
            name = job['metadata']['name']
            self.kubectl(['wait', '-n', PREFIX, '--for=condition=complete', f'job/{name}', '--timeout=600s'])
            observed = json.loads(self.kubectl(['get', 'job', name, '-n', PREFIX, '-o', 'json']))
            require(observed['status'].get('succeeded', 0) > 0 and not observed['status'].get('failed', 0), 'MIGRATION_JOB_FAILED', name)
            self.receipt('migration' if name.startswith('auth-migrate-') else 'bootstrap', dict(verified=True,
                job=name, uid=observed['metadata']['uid'], image=observed['spec']['template']['spec']['containers'][0]['image']))

    def diagnostics(self):
        kubeconfig = os.getenv('KUBECONFIG')
        require(kubeconfig and Path(kubeconfig).is_file()
            and Path(kubeconfig).resolve().is_relative_to(Path(self.temporary.name).resolve()), 'SCOPED_KUBECONFIG_UNAVAILABLE')
        self.run(['node', str(ROOT / 'scripts/environment-actions/collect.mjs')], timeout=180)

    def destroy_stack(self, stack):
        state_key = f'phase4/environments/demo-a/{stack}.tfstate'
        try:
            self.api('s3', 'head_object', Bucket=self.bucket, Key=state_key, ExpectedBucketOwner=ACCOUNT)
        except Failure as error:
            if error.code in ('404', 'NoSuchKey', 'NotFound'):
                require(not self.journal['resources'].get(stack), 'STATE_MISSING_WITH_INVENTORY', stack)
                return
            raise
        self.init(stack)
        resources = self.snapshot(stack)
        if not resources:
            return
        require(stack in self.journal['stacks'], 'RECOVERY_INPUTS_REQUIRED', stack)
        inputs = Path(self.temporary.name) / (stack + '-destroy.json')
        inputs.write_bytes(canonical(self.journal['stacks'][stack]))
        results = self.report.data.setdefault('terraform_stacks', {})
        results[stack] = 'DESTROY_RUNNING'
        try:
            self.tf(stack, ['destroy', '-json', '-input=false', '-auto-approve', '-lock-timeout=60s', f'-var-file={inputs}'])
        except BaseException:
            results[stack] = 'DESTROY_FAILED'
            self.report.data['terraform_result'] = 'FAILED'
            try:
                with self.report.phase('checkpoint-after-failed-destroy', stack):
                    self.snapshot(stack)
            except Exception:
                pass
            raise
        results[stack] = 'DESTROYED'
        self.report.data['terraform_result'] = 'DESTROYED_OWNED_STACKS'
        self.snapshot(stack)

    def destroy(self):
        cluster = self.cluster(missing_ok=True)
        if cluster:
            receipt = self.journal['receipts'].get('teardown', {})
            require(receipt.get('verified') is True and receipt.get('cluster_created_at') == str(cluster['createdAt']), 'PRIVILEGED_TEARDOWN_REQUIRED')
            require(not self.owned_load_balancers() and not self.data_volumes(), 'KUBERNETES_EXTERNAL_RESOURCES_REMAIN')
        with self.report.phase('destroy-edge', 'edge'):
            self.destroy_stack('edge')
        with self.report.phase('destroy-platform', 'platform'):
            self.destroy_stack('platform')
        with self.report.phase('destroy-external-artifacts', PREFIX):
            self.cleanup_external()
        with self.report.phase('destroy-infra', 'infra'):
            self.destroy_stack('infra')
        with self.report.phase('residual-verification', PREFIX):
            self.residuals()

    def cleanup_kubernetes(self):
        with self.report.phase('block-ingress-and-remove-alb', PREFIX):
            for balancer in self.owned_load_balancers():
                groups = self.items('elbv2', 'describe_target_groups', 'TargetGroups', LoadBalancerArn=balancer['LoadBalancerArn'])
                self.journal['external_arns'] = sorted(set(self.journal.get('external_arns', [])
                    + [balancer['LoadBalancerArn']] + [group['TargetGroupArn'] for group in groups]))
                self.save()
            for resource in self.journal['resources'].get('edge', []):
                if resource['type'] != 'aws_apigatewayv2_api':
                    continue
                try:
                    api = self.api('apigatewayv2', 'get_api', ApiId=resource['id'])
                except Failure as error:
                    if error.code == 'NotFoundException':
                        continue
                    raise
                require(api.get('Tags', {}).get('EnvironmentId') == 'demo-a', 'API_OWNERSHIP_REJECTED')
                self.api('apigatewayv2', 'update_api', ApiId=api['ApiId'], DisableExecuteApiEndpoint=True)
            namespaces = json.loads(self.kubectl(['get', 'namespaces', '-o', 'json']))['items']
            namespace = next((item for item in namespaces if item['metadata']['name'] == PREFIX), None)
            if namespace:
                require(namespace['metadata'].get('labels', {}).get('oficina/environment') == 'demo-a', 'NAMESPACE_OWNERSHIP_REJECTED')
                self.kubectl(['delete', 'ingress', '--all', '-n', PREFIX, '--wait=true', '--timeout=300s'])
            self.wait(lambda: not self.owned_load_balancers(), 'ALB_CLEANUP_TIMEOUT')
        with self.report.phase('remove-workloads-and-pvcs', PREFIX):
            volumes = json.loads(self.kubectl(['get', 'pv', '-o', 'json']))['items']
            volume_ids = [volume['spec']['csi']['volumeHandle'] for volume in volumes
                if volume['spec'].get('claimRef', {}).get('namespace') == PREFIX and volume['spec'].get('csi', {}).get('driver') == 'ebs.csi.aws.com']
            self.journal['data_volumes'] = sorted(set(self.journal.get('data_volumes', []) + volume_ids))
            self.save()
            if namespace:
                self.kubectl(['delete', 'deployment,statefulset,job', '--all', '-n', PREFIX, '--wait=true', '--timeout=300s'])
                self.kubectl(['delete', 'pvc', '--all', '-n', PREFIX, '--wait=true', '--timeout=300s'])
            self.wait(lambda: not self.data_volumes(), 'EBS_CLEANUP_TIMEOUT')
        with self.report.phase('remove-helm-and-cluster-manifests', PREFIX):
            releases = json.loads(self.run(['helm', 'list', '--all-namespaces', '-o', 'json']))
            for release in releases:
                if (release['name'], release['namespace']) in [(PREFIX + '-alb', 'kube-system'), (PREFIX + '-observability', PREFIX + '-observability')]:
                    values = json.loads(self.run(['helm', 'get', 'values', release['name'], '-n', release['namespace'], '-o', 'json']))
                    require(values.get('clusterName') == PREFIX or values.get('global', {}).get('cluster') == PREFIX, 'HELM_OWNERSHIP_REJECTED')
                    self.run(['helm', 'uninstall', release['name'], '-n', release['namespace'], '--wait', '--timeout=300s'])
            for kind, name in [('storageclass', PREFIX + '-gp3'), ('ingressclass', PREFIX + '-edge'),
                               ('clusterrolebinding', PREFIX + '-evidence'), ('clusterrole', PREFIX + '-evidence')]:
                existing = json.loads(self.kubectl(['get', kind, '-o', 'json']))['items']
                resource = next((item for item in existing if item['metadata']['name'] == name), None)
                if resource:
                    require(resource['metadata'].get('labels', {}).get('oficina/environment') == 'demo-a', 'CLUSTER_RESOURCE_OWNERSHIP_REJECTED')
                    self.kubectl(['delete', kind, name])
            for name in [PREFIX, PREFIX + '-observability']:
                if any(item['metadata']['name'] == name for item in namespaces):
                    current = next(item for item in namespaces if item['metadata']['name'] == name)
                    require(current['metadata'].get('labels', {}).get('oficina/environment') == 'demo-a', 'NAMESPACE_OWNERSHIP_REJECTED')
                    self.kubectl(['delete', 'namespace', name, '--wait=true', '--timeout=300s'])

    def owned_load_balancers(self):
        matches = []
        for load_balancer in self.items('elbv2', 'describe_load_balancers', 'LoadBalancers'):
            if load_balancer['LoadBalancerName'] == PREFIX + '-edge':
                tags = self.api('elbv2', 'describe_tags', ResourceArns=[load_balancer['LoadBalancerArn']])['TagDescriptions'][0]['Tags']
                require({tag['Key']: tag['Value'] for tag in tags}.get('EnvironmentId') == 'demo-a', 'ALB_OWNERSHIP_REJECTED')
                matches.append(load_balancer)
        return matches

    def owned_volumes(self):
        return self.items('ec2', 'describe_volumes', 'Volumes', Filters=[dict(Name='tag:EnvironmentId', Values=['demo-a'])])

    def data_volumes(self):
        remaining = []
        for identifier in self.journal.get('data_volumes', []):
            try:
                volumes = self.api('ec2', 'describe_volumes', VolumeIds=[identifier])['Volumes']
                remaining.extend(volumes)
            except Failure as error:
                if error.code != 'InvalidVolume.NotFound':
                    raise
        return remaining

    def cleanup_external(self):
        from live_release import cleanup_release
        from live_secrets import cleanup_secrets
        cleanup_release(self)
        cleanup_secrets(self)

    def residuals(self):
        require(not self.cluster(missing_ok=True), 'RESIDUAL_EKS')
        require(not self.owned_load_balancers(), 'RESIDUAL_ALB')
        require(not self.owned_volumes(), 'RESIDUAL_EBS')
        try:
            databases = self.api('rds', 'describe_db_instances', DBInstanceIdentifier=PREFIX + '-postgres')['DBInstances']
        except Failure as error:
            if error.code != 'DBInstanceNotFound':
                raise
            databases = []
        require(not databases, 'RESIDUAL_RDS')
        filters = [dict(Name='vpc-id', Values=[self.journal['vpc_id']])] if self.journal.get('vpc_id') else [dict(Name='tag:EnvironmentId', Values=['demo-a'])]
        interfaces = self.items('ec2', 'describe_network_interfaces', 'NetworkInterfaces', Filters=filters)
        require(not interfaces, 'RESIDUAL_ENI')
        for arn in self.journal.get('external_arns', []):
            if ':targetgroup/' in arn:
                try:
                    groups = self.api('elbv2', 'describe_target_groups', TargetGroupArns=[arn])['TargetGroups']
                except Failure as error:
                    if error.code != 'TargetGroupNotFound':
                        raise
                    groups = []
                require(not groups, 'RESIDUAL_TARGET_GROUP', arn)
        for entry in self.journal['objects']:
            self.require_absent('s3', 'head_object', {'404', 'NotFound', 'NoSuchKey', 'NoSuchVersion', 'NoSuchBucket'},
                'RESIDUAL_ARTIFACT_VERSION', Bucket=entry['bucket'], Key=entry['key'], VersionId=entry['version_id'], ExpectedBucketOwner=ACCOUNT)
        for arn in self.journal['layers']:
            self.require_absent('lambda', 'get_layer_version', {'ResourceNotFoundException'}, 'RESIDUAL_LAYER',
                LayerName=PREFIX + '-auth-bootstrap', VersionNumber=int(arn.split(':')[-1]))
        for arn in self.journal['secrets'].values():
            self.require_absent('secretsmanager', 'describe_secret', {'ResourceNotFoundException'}, 'RESIDUAL_OR_PENDING_SECRET', SecretId=arn)
        for service in ('auth', 'os', 'execution', 'billing'):
            self.require_absent('ecr', 'describe_repositories', {'RepositoryNotFoundException'}, 'RESIDUAL_ECR', repositoryNames=[PREFIX + '-' + service])
        self.receipt('destroy', dict(verified=True, cost_status='UNVERIFIED_BILLING_LAG', backend_preserved=True))

    def require_absent(self, service, operation, missing_codes, residual_code, **arguments):
        try:
            self.api(service, operation, **arguments)
        except Failure as error:
            if error.code in missing_codes:
                return
            raise
        raise Failure(residual_code, service + ':' + operation)

    def teardown(self):
        for stack in STACKS:
            try:
                self.api('s3', 'head_object', Bucket=self.bucket, Key=f'phase4/environments/demo-a/{stack}.tfstate', ExpectedBucketOwner=ACCOUNT)
            except Failure as error:
                if error.code in ('404', 'NoSuchKey', 'NotFound'):
                    require(not self.journal['resources'].get(stack), 'STATE_MISSING_WITH_INVENTORY', stack)
                    continue
                raise
            with self.report.phase('recover-partial-state-inventory', stack):
                self.init(stack)
                self.snapshot(stack)
        cluster = self.cluster(missing_ok=True)
        if cluster:
            with self.kubernetes():
                self.cleanup_kubernetes()
        self.receipt('teardown', dict(verified=True, cluster_created_at=str(cluster['createdAt']) if cluster else None))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['up', 'destroy', 'teardown', 'close'])
    parser.add_argument('--phase', default='infra')
    parser.add_argument('--approved-plan-sha256', default='')
    parser.add_argument('--execution-mode', choices=['plan', 'apply'], default='plan')
    options = parser.parse_args()
    report = Report(options.operation)
    driver = None
    try:
        configuration = json.loads((ROOT / 'phase4/environments/demo-a.runtime.json').read_text())
        catalog = json.loads((ROOT / 'phase4/environments/catalog.json').read_text())['environments']['demo-a']
        with report.phase('authorization', options.operation):
            if options.operation != 'close':
                expected_role = 'destroy' if options.operation == 'destroy' else 'provision'
                if options.operation == 'up' and options.phase in ('release', 'secrets', 'data', 'migrate', 'apps', 'health', 'jwks', 'alb-check', 'e2e'):
                    expected_role = 'deploy'
                require(os.getenv('EXECUTOR_ROLE') == expected_role, 'EXECUTOR_ROLE_REJECTED')
            operation = 'destroy' if options.operation == 'teardown' else options.operation
            if operation == 'close':
                operation = 'up' if os.environ.get('CONFIRMATION') == 'UP demo-a' else 'destroy'
                guard(operation, configuration, {**catalog, 'enabled': True}, os.environ, cleanup=True)
            else:
                if operation == 'up':
                    require(options.execution_mode != 'plan' or not options.approved_plan_sha256, 'PLAN_HASH_NOT_ALLOWED')
                    require(options.execution_mode != 'apply' or options.phase.split('-')[0] not in STACKS
                            or re.fullmatch(r'[0-9a-f]{64}', options.approved_plan_sha256), 'APPROVED_PLAN_HASH_REQUIRED')
                execution_configuration = json.loads((ROOT / 'phase4/environments/actions-first.json').read_text())
                guard(operation, configuration, catalog, execution_mode=options.execution_mode if operation == 'up' else 'apply',
                      phase=options.phase, execution_configuration=execution_configuration)
        driver = Driver(configuration, report, catalog=catalog)
        with report.phase('identity-backend-inventory', PREFIX):
            driver.load()
        if options.operation == 'destroy':
            driver.destroy()
        elif options.operation == 'teardown':
            driver.teardown()
        elif options.operation == 'close':
            driver.close_endpoint()
        elif options.phase.split('-')[0] in STACKS:
            with report.phase('terraform-plan-apply', options.phase) as entry:
                entry['outcome'] = driver.plan_apply(options.phase.split('-')[0], options.approved_plan_sha256, options.phase, options.execution_mode)
        else:
            from delivery import up_phase
            with report.phase('delivery', options.phase):
                up_phase(driver, options.phase)
    except BaseException as error:
        if not any(entry['status'] == 'FAIL' for entry in report.data['checks']):
            try:
                with report.phase('executor', options.phase):
                    raise error
            except BaseException:
                pass
    finally:
        if driver:
            driver.temporary.cleanup()
        report.finish()
    return 1 if report.data['result'] == 'FAIL' else 0


if __name__ == '__main__':
    sys.modules['runtime'] = sys.modules[__name__]
    sys.exit(main())
