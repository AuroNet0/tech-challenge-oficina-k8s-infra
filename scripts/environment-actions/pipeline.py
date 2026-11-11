import argparse
import base64
import copy
import contextlib
import json
import os
from pathlib import Path
import re
import sys

from runtime import ACCOUNT, REGION, ROOT, PREFIX, STACKS, Driver, Failure, Report, canonical, digest, require


PHASES = ('infra', 'release', 'platform', 'foundation', 'controller', 'secrets', 'data',
          'platform-migration', 'migrate', 'edge', 'edge-auth', 'jwks', 'platform-apps',
          'apps', 'edge-alb', 'ingress', 'alb-check', 'edge-business', 'observability',
          'health', 'e2e', 'diagnose', 'recovery-closed', 'recovery-expiry',
          'iam-maintenance', 'artifact-foundation', 'recovery-foundation', 'recovery-activate')
MAINTENANCE_PHASES = ('iam-maintenance', 'artifact-foundation', 'recovery-foundation', 'recovery-activate')
DEPLOY_PHASES = ('release', 'secrets', 'data', 'migrate', 'apps', 'health', 'jwks', 'alb-check', 'e2e')
REPOSITORY = 'AuroNet0/tech-challenge-oficina-k8s-infra'


def role_for(phase):
    if phase.startswith('recovery-') or phase in MAINTENANCE_PHASES:
        return 'bootstrap'
    return 'deploy' if phase in DEPLOY_PHASES else 'provision'


def scope(operation, phase, environment=None):
    environment = os.environ if environment is None else environment
    require(environment.get('GITHUB_REPOSITORY') == REPOSITORY
            and environment.get('GITHUB_REF') == 'refs/heads/homolog'
            and environment.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', 'DISPATCH_SCOPE_REJECTED')
    require(re.fullmatch('[0-9a-f]{40}', environment.get('GITHUB_SHA', '')), 'REVISION_REQUIRED')
    require(phase in PHASES, 'PHASE_REJECTED')
    if operation in ('destroy', 'teardown'):
        require(environment.get('CONFIRMATION') == 'DESTROY demo-a', 'CONFIRMATION_REJECTED')
    if operation != 'plan':
        expected = (environment.get('EXECUTOR_ROLE') if operation == 'close' else
                    'destroy' if operation == 'destroy' else 'provision' if operation == 'teardown' else role_for(phase))
        require(expected in ('provision', 'deploy', 'destroy', 'bootstrap'), 'EXECUTOR_ROLE_REJECTED')
        require(environment.get('PROTECTED_ENVIRONMENT') == f'phase4-demo-a-{expected}', 'ENVIRONMENT_APPROVAL_REQUIRED')


def gh_json(arguments, source=False):
    import subprocess
    environment = dict(os.environ)
    if source and environment.get('SOURCE_READ_TOKEN'):
        environment['GH_TOKEN'] = environment['SOURCE_READ_TOKEN']
    result = subprocess.run(['gh', 'api', *arguments], capture_output=True, timeout=60, env=environment)
    require(result.returncode == 0, 'GITHUB_API_FAILED', ' '.join(arguments[:1]))
    return json.loads(result.stdout)


def protection():
    name = os.environ['PROTECTED_ENVIRONMENT']
    document = gh_json([f'repos/{REPOSITORY}/environments/{name}'])
    require(any(rule.get('type') == 'required_reviewers' and rule.get('reviewers')
                for rule in document.get('protection_rules', [])), 'ENVIRONMENT_REVIEWERS_REQUIRED', name)


def configuration():
    config = json.loads((ROOT / 'phase4/environments/demo-a.runtime.json').read_text())
    catalog = json.loads((ROOT / 'phase4/environments/catalog.json').read_text())['environments']['demo-a']
    require(config.get('account') == ACCOUNT and config.get('region') == REGION
            and config.get('environment') == 'demo-a', 'CONFIGURATION_SCOPE_REJECTED')
    require(config.get('backend_bucket') == catalog.get('backend_bucket')
            and catalog.get('aws_account_id') == ACCOUNT and catalog.get('aws_region') == REGION,
            'CATALOG_BACKEND_MISMATCH')
    require(all(catalog['state_keys'][stack] == f'phase4/environments/demo-a/{stack}.tfstate' for stack in STACKS),
            'STATE_KEY_REJECTED')
    repositories = dict(auth='auth', os='os-service', execution='execution-service', billing='billing-service')
    for service, repository in repositories.items():
        revision = os.getenv('SOURCE_' + service.upper() + '_REVISION')
        if revision:
            config['services'][service] = dict(repository='AuroNet0/tech-challenge-oficina-' + repository,
                                              revision=revision,
                                              required_checks=[name.strip() for name in os.getenv('SOURCE_' + service.upper() + '_CHECKS', '').split(',') if name.strip()])
    for variable, field in (('MERCADO_PAGO_TEST_SECRET_ARN', 'mercado_pago_test_secret_arn'),
                            ('NEW_RELIC_SECRET_ARN', 'new_relic_secret_arn'), ('NETWORK_PROBE_IMAGE', 'network_probe_image')):
        if os.getenv(variable):
            config[field] = os.environ[variable]
    platform = config['terraform_inputs']['platform']
    for name in ('mongodb', 'rabbitmq', 'postgres'):
        if os.getenv(name.upper() + '_IMAGE'):
            platform.setdefault('images', {})[name] = os.environ[name.upper() + '_IMAGE']
    for name, variable in (('vpc_cni', 'EKS_VPC_CNI_VERSION'), ('ebs_csi', 'EKS_EBS_CSI_VERSION'), ('pod_identity', 'EKS_POD_IDENTITY_VERSION')):
        if os.getenv(variable):
            platform.setdefault('addon_versions', {})[name] = os.environ[variable]
    platform.setdefault('workload_permissions_boundaries', {}).update({name: f'arn:aws:iam::{ACCOUNT}:policy/{PREFIX}-{name}-boundary'
                                                                     for name in ('ebs-csi', 'load-balancer-controller')})
    edge = config['terraform_inputs']['edge']
    edge.setdefault('auth_permissions_boundary_arn', f'arn:aws:iam::{ACCOUNT}:policy/{PREFIX}-auth-lambda-boundary')
    if os.getenv('ALB_CERTIFICATE_ARN'):
        require(os.getenv('ALB_SERVER_NAME'), 'ALB_SERVER_NAME_REQUIRED')
        edge['alb_tls'] = dict(certificate_arn=os.environ['ALB_CERTIFICATE_ARN'], server_name=os.environ['ALB_SERVER_NAME'])
    return config, catalog


def artifact_key():
    try:
        key = base64.b64decode(os.getenv('PLAN_ARTIFACT_KEY', ''), validate=True)
    except ValueError:
        key = b''
    require(len(key) == 32, 'PLAN_ARTIFACT_KEY_REQUIRED', 'GitHub secret PLAN_ARTIFACT_KEY (base64, 32 bytes)')
    return key


def technical_inputs(config, phase):
    if phase == 'e2e':
        raise Failure('AWS_E2E_ADAPTER_REQUIRED', 'Billing integration/9.1.4 currently uses Docker and mock payments')
    if phase == 'release':
        for name in ('auth', 'os', 'execution', 'billing'):
            source = config.get('services', {}).get(name, {})
            require(re.fullmatch('[0-9a-f]{40}', source.get('revision', '')) and source.get('required_checks'),
                    'SOURCE_REVISION_AND_CHECKS_REQUIRED', 'GitHub variables SOURCE_' + name.upper() + '_REVISION / _CHECKS')
    if phase.startswith('platform'):
        values = config['terraform_inputs']['platform']
        for name in ('mongodb', 'rabbitmq', 'postgres'):
            require(re.fullmatch(r'[a-z0-9./:_-]+@sha256:[0-9a-f]{64}', values.get('images', {}).get(name, '')),
                    'PINNED_DATA_IMAGE_REQUIRED', name.upper() + '_IMAGE')
        require(set(values.get('addon_versions', {})) == {'vpc_cni', 'ebs_csi', 'pod_identity'}, 'PINNED_ADDON_VERSIONS_REQUIRED')
    if phase == 'foundation':
        require(re.fullmatch(r'[a-z0-9./:_-]+@sha256:[0-9a-f]{64}', config.get('network_probe_image', '')),
                'PINNED_NETWORK_PROBE_IMAGE_REQUIRED', 'NETWORK_PROBE_IMAGE')
    if phase == 'secrets':
        require(config.get('mercado_pago_test_secret_arn'), 'MERCADO_PAGO_TEST_SECRET_REQUIRED')
    if phase == 'observability':
        require(config.get('new_relic_secret_arn'), 'NEW_RELIC_SECRET_REQUIRED')


def seal(payload, manifest):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return nonce + AESGCM(artifact_key()).encrypt(nonce, canonical(payload), canonical(manifest))


def unseal(payload, manifest):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        return json.loads(AESGCM(artifact_key()).decrypt(payload[:12], payload[12:], canonical(manifest)))
    except Failure:
        raise
    except Exception:
        raise Failure('PLAN_ARTIFACT_AUTHENTICATION_FAILED') from None


def state_versions(driver):
    versions = {}
    keys = getattr(driver, 'state_keys', {stack: f'phase4/environments/demo-a/{stack}.tfstate' for stack in STACKS})
    for stack, key in keys.items():
        try:
            state = driver.api('s3', 'head_object', Bucket=driver.bucket, Key=key, ExpectedBucketOwner=ACCOUNT)
            versions[stack] = dict(version=state.get('VersionId'), etag=state['ETag'])
        except Failure as error:
            if error.code not in ('404', 'NoSuchKey', 'NotFound'):
                raise
            versions[stack] = None
    return versions


def contract_hash(driver):
    return digest(canonical(dict(configuration=driver.config, catalog=driver.catalog, inventory=driver.journal)))


def plan(driver, phase):
    manifest = dict(schema_version=1, operation='deploy', environment='demo-a', phase=phase,
                    revision=os.environ['GITHUB_SHA'], account=ACCOUNT, region=REGION,
                    backend=driver.bucket, states=state_versions(driver), contract_sha256=contract_hash(driver),
                    terraform_version='1.15.8', run_id=os.environ['GITHUB_RUN_ID'])
    payload = {}
    stack = 'identities' if phase in MAINTENANCE_PHASES else phase.split('-')[0]
    if stack in (*STACKS, 'identities'):
        driver.init(stack)
        values = driver.maintenance_inputs if stack == 'identities' else driver.variables(stack, phase)
        if stack != 'identities':
            values['approval_source'] = 'github-environment'
        if stack == 'infra':
            values['planning_only'] = False
        inputs = Path(driver.temporary.name) / 'inputs.json'
        inputs.write_bytes(canonical(values))
        saved = Path(driver.temporary.name) / 'approved.tfplan'
        driver.tf(stack, ['fmt', '-check'])
        driver.tf(stack, ['validate', '-json'])
        driver.tf(stack, ['plan', '-json', '-input=false', '-lock-timeout=60s', f'-var-file={inputs}', f'-out={saved}'])
        document = json.loads(driver.tf(stack, ['show', '-json', str(saved)]))
        if stack == 'identities':
            driver.review(document)
        require(not document.get('errored') and not document.get('deferred_changes'), 'PLAN_INCOMPLETE')
        changes = [change for change in document.get('resource_changes', []) if change.get('mode') != 'data']
        require(not any('delete' in change['change']['actions'] for change in changes), 'UP_REPLACEMENT_REQUIRES_SEPARATE_REVIEW')
        manifest.update(stack=stack, binary_sha256=digest(saved.read_bytes()),
                        changes=[dict(address=change['address'], actions=change['change']['actions']) for change in changes])
        payload.update(binary=base64.b64encode(saved.read_bytes()).decode(), variables=values)
    else:
        from delivery import validate_phase
        validate_phase(driver, phase)
        manifest['checkpoint'] = phase
    require(manifest['states'] == state_versions(driver) and manifest['contract_sha256'] == contract_hash(driver),
            'STATE_CHANGED_DURING_PLAN')
    encrypted = seal(payload, manifest)
    (driver.report.directory / 'approved-plan.enc').write_bytes(encrypted)
    summary = dict(manifest=manifest, sha256=digest(canonical(manifest) + encrypted))
    (driver.report.directory / 'plan.json').write_bytes(canonical(summary))
    driver.report.data['plan_sha256'] = summary['sha256']
    driver.report.data['terraform_result'] = 'PLAN_ONLY' if stack in (*STACKS, 'identities') else 'CHECKPOINT_REVIEW_ONLY'


def approved(driver, phase, reference, approved_hash, directory):
    require(re.fullmatch('[0-9]+', reference), 'PLAN_RUN_REQUIRED')
    require(re.fullmatch('[0-9a-f]{64}', approved_hash), 'APPROVED_PLAN_HASH_REQUIRED')
    run = gh_json([f'repos/{REPOSITORY}/actions/runs/{reference}'])
    require(run.get('path') == '.github/workflows/plan.yml' and run.get('event') == 'workflow_dispatch'
            and run.get('head_branch') == 'homolog' and run.get('head_sha') == os.environ['GITHUB_SHA']
            and run.get('conclusion') == 'success', 'PLAN_RUN_PROVENANCE_REJECTED')
    directory = Path(directory)
    summary = json.loads((directory / 'plan.json').read_text())
    manifest = summary['manifest']
    encrypted = (directory / 'approved-plan.enc').read_bytes()
    require(approved_hash == summary['sha256'] == digest(canonical(manifest) + encrypted), 'APPROVED_PLAN_HASH_MISMATCH')
    require(manifest.get('run_id') == reference and manifest.get('phase') == phase
            and manifest.get('revision') == os.environ['GITHUB_SHA'] and manifest.get('environment') == 'demo-a'
            and manifest.get('account') == ACCOUNT and manifest.get('region') == REGION
            and manifest.get('backend') == driver.bucket and manifest.get('terraform_version') == '1.15.8', 'PLAN_CONTEXT_MISMATCH')
    require(manifest['states'] == state_versions(driver), 'TERRAFORM_STATE_CHANGED_REPLAN_REQUIRED')
    require(manifest['contract_sha256'] == contract_hash(driver), 'INVENTORY_OR_CONFIGURATION_CHANGED_REPLAN_REQUIRED')
    return manifest, unseal(encrypted, manifest)


def deploy(driver, phase, reference, approved_hash, directory):
    manifest, payload = approved(driver, phase, reference, approved_hash, directory)
    driver.authorization_source = 'github-environment'
    if manifest.get('stack'):
        stack = manifest['stack']
        require((stack in STACKS and stack == phase.split('-')[0])
                or (stack == 'identities' and phase in MAINTENANCE_PHASES), 'STACK_REJECTED')
        binary = base64.b64decode(payload['binary'], validate=True)
        require(digest(binary) == manifest['binary_sha256'], 'APPROVED_PLAN_HASH_MISMATCH')
        if stack in ('platform', 'edge'):
            driver.verify_attestations(stack, payload['variables'])
        saved = Path(driver.temporary.name) / 'approved.tfplan'
        saved.write_bytes(binary)
        driver.init(stack)
        require(manifest['states'] == state_versions(driver), 'TERRAFORM_STATE_CHANGED_REPLAN_REQUIRED')
        if stack != 'identities':
            driver.journal['stacks'][stack] = payload['variables']
            driver.save()
        try:
            driver.tf(stack, ['apply', '-json', '-input=false', '-lock-timeout=60s', str(saved)])
            driver.report.data['terraform_result'] = 'APPLIED_APPROVED_BINARY'
        except BaseException:
            try:
                with driver.report.phase('checkpoint-after-failed-apply', stack):
                    if stack != 'identities':
                        driver.snapshot(stack)
            except Exception:
                pass
            raise
        if stack != 'identities':
            driver.snapshot(stack)
    elif phase.startswith('recovery-'):
        from verify_recovery import verify
        os.environ['VERIFY_MODE'] = 'closed-canary' if phase == 'recovery-closed' else 'lease-expiry'
        verify(driver)
    else:
        from delivery import up_phase
        if phase == 'diagnose':
            with driver.kubernetes():
                driver.diagnostics()
        else:
            up_phase(driver, phase)


class PipelineDriver(Driver):
    @contextlib.contextmanager
    def kubernetes(self):
        if self.config.get('endpoint', {}).get('mode') == 'broker':
            require(self.invoke('recovery', {'probe': True}).get('verified') is True, 'LIVE_RECOVERY_PROBE_FAILED')
            self.config = copy.deepcopy(self.config)
            self.config['endpoint']['independent_canary_verified'] = True
        with super().kubernetes():
            yield

    def destroy(self):
        try:
            self.destroy_owned()
        except BaseException:
            try:
                with self.report.phase('residual-verification-after-failure', PREFIX):
                    self.residuals()
            except BaseException:
                pass
            raise

    def destroy_owned(self):
        cluster = self.cluster(missing_ok=True)
        receipt = self.journal['receipts'].get('teardown', {})
        valid_teardown = cluster and receipt.get('verified') and receipt.get('cluster_created_at') == str(cluster['createdAt'])
        if cluster and not valid_teardown:
            require(not self.journal['resources'].get('platform') and not self.journal['resources'].get('edge')
                    and not self.owned_load_balancers() and not self.data_volumes(), 'PRIVILEGED_TEARDOWN_REQUIRED')
            for stack in ('platform', 'edge'):
                require(state_versions(self)[stack] is None, 'PRIVILEGED_TEARDOWN_REQUIRED', stack)
            self.receipt('teardown', dict(verified=True, cluster_created_at=str(cluster['createdAt']),
                                         recovery='INFRA_ONLY_NO_KUBERNETES_WORKLOAD_STATE'))
        super().destroy()


class RecoveryDriver(PipelineDriver):
    state_keys = dict(identities='phase4/bootstrap/identities/demo-a.tfstate')

    def load(self):
        identity = self.api('sts', 'get_caller_identity')
        require(identity['Account'] == ACCOUNT and identity['Arn'].startswith(
            f'arn:aws:sts::{ACCOUNT}:assumed-role/{PREFIX}-bootstrap/'), 'BOOTSTRAP_IDENTITY_REJECTED')
        cluster = self.cluster()
        self.config = dict(self.config, recovery_cluster=dict(arn=cluster['arn'], generation=str(cluster['createdAt'])))
        receipt = ROOT / 'actions-report/execution/endpoint-lease.json'
        if receipt.exists():
            lease = json.loads(receipt.read_text())
            require(lease['run_id'] == os.environ['GITHUB_RUN_ID'] and lease['revision'] == os.environ['GITHUB_SHA']
                    and re.fullmatch('[0-9a-f]{32}', lease['lease_id']), 'ENDPOINT_LEASE_SCOPE_REJECTED')
            self.lease = lease['lease_id']


class MaintenanceDriver(PipelineDriver):
    state_keys = dict(identities='phase4/bootstrap/identities/demo-a.tfstate')

    def __init__(self, configuration, report, phase, **arguments):
        super().__init__(configuration, report, **arguments)
        inputs = json.loads((ROOT / 'phase4/bootstrap/identities/approved-inputs.json').read_text())
        require(inputs.get('subject_format') == 'immutable' and inputs.get('backend_bucket') == self.bucket,
                'IMMUTABLE_BOOTSTRAP_CONTRACT_REQUIRED')
        if phase == 'artifact-foundation':
            inputs['artifact_bucket_installation_approved'] = True
        if phase == 'recovery-foundation':
            inputs.update(recovery_installation_approved=True, recovery_package_path='endpoint-recovery.zip',
                          recovery_alarm_topic_arn=os.getenv('RECOVERY_ALARM_TOPIC_ARN', ''))
        if phase == 'recovery-activate':
            inputs.update(recovery_installation_approved=True, recovery_opening_approved=True, recovery_verification_approved=True)
        self.maintenance_inputs = inputs
        self.requested_external_inputs = {name: configuration[field] for name, field in
            (('newrelic', 'new_relic_secret_arn'), ('mercado-pago', 'mercado_pago_test_secret_arn')) if configuration.get(field)}
        inputs.setdefault('external_input_secrets', {}).update(self.requested_external_inputs)
        self.phase = phase
        if phase == 'iam-maintenance':
            self.state_keys = dict(self.state_keys, **{stack: f'phase4/environments/demo-a/{stack}.tfstate' for stack in STACKS})
        self.config = dict(configuration, maintenance_inputs=inputs)

    def load(self):
        identity = self.api('sts', 'get_caller_identity')
        require(identity['Account'] == ACCOUNT and identity['Arn'].startswith(
            f'arn:aws:sts::{ACCOUNT}:assumed-role/{PREFIX}-bootstrap/'), 'BOOTSTRAP_IDENTITY_REJECTED')
        if self.phase == 'iam-maintenance':
            observer = Driver(self.config, self.report, clients=self.clients, execute=self.execute, catalog=self.catalog)
            try:
                observer.load()
                for stack, version in state_versions(observer).items():
                    if version is not None:
                        observer.init(stack)
                        observer.snapshot(stack, persist=False)
                    else:
                        require(not observer.journal['resources'].get(stack), 'STATE_MISSING_WITH_INVENTORY', stack)
                self.journal = observer.journal
                self.maintenance_inputs.update(inventory=observer.identity_inventory(), inventory_approved=True,
                    destroy_confirmation=f'DESTROY demo-a {ACCOUNT} {PREFIX}')
                (self.report.directory / 'identity-inventory.json').write_bytes(canonical(dict(inventory=self.maintenance_inputs['inventory'],
                    approval_source='GitHub Environment approval of this IAM plan; not AWS eligibility evidence')))
            finally:
                observer.temporary.cleanup()
        self.init('identities')
        version = state_versions(self)['identities']
        if version is None:
            raise Failure('BOOTSTRAP_STATE_REQUIRED_NO_AUTOMATIC_RECREATION', self.state_keys['identities'])
        else:
            document = json.loads(self.tf('identities', ['show', '-json']))
            outputs = document.get('values', {}).get('outputs', {})
            saved_inventory = outputs.get('maintenance_inventory', {}).get('value')
            if self.phase != 'iam-maintenance' and saved_inventory:
                self.maintenance_inputs.update(saved_inventory)
                self.maintenance_inputs.setdefault('external_input_secrets', {}).update(self.requested_external_inputs)
            if self.phase != 'iam-maintenance' and not saved_inventory:
                require(not outputs.get('identity_contract', {}).get('value', {}).get('workload_boundaries'),
                        'IAM_INVENTORY_CHECKPOINT_REQUIRED', 'Plan/Deploy iam-maintenance preserves the existing scoped inventory')

            def preserve(module):
                for resource in module.get('resources', []):
                    values = resource.get('values', {})
                    if resource['type'] == 'aws_s3_bucket' and values.get('bucket') == PREFIX + '-artifacts':
                        self.maintenance_inputs['artifact_bucket_installation_approved'] = True
                    if resource['type'] == 'aws_lambda_function' and values.get('function_name') == PREFIX + '-endpoint-recovery':
                        settings = json.loads(values['environment'][0]['variables']['RECOVERY_CONFIG'])
                        require(settings['account'] == ACCOUNT and settings['environment_id'] == 'demo-a', 'RECOVERY_SCOPE_REJECTED')
                        self.maintenance_inputs.update(recovery_installation_approved=True, recovery_package_path='endpoint-recovery.zip',
                                                      recovery_reconcile_interval_minutes=settings['reconcile_interval_minutes'])
                        if settings['enabled']:
                            self.maintenance_inputs.update(recovery_opening_approved=True, recovery_verification_approved=True)
                    if resource['type'] == 'aws_cloudwatch_metric_alarm' and values.get('alarm_name') == PREFIX + '-recovery-errors':
                        self.maintenance_inputs.setdefault('recovery_alarm_topic_arn', values['alarm_actions'][0])
                        if not self.maintenance_inputs['recovery_alarm_topic_arn']:
                            self.maintenance_inputs['recovery_alarm_topic_arn'] = values['alarm_actions'][0]
                for child in module.get('child_modules', []):
                    preserve(child)

            preserve(document.get('values', {}).get('root_module', {}))
        if self.report.data.get('phase') == 'recovery-activate':
            require(self.invoke('recovery', {'probe': True}).get('verified') is True, 'LIVE_RECOVERY_PROBE_FAILED')
        source = Path(self.temporary.name) / 'inputs.json'
        source.write_bytes(canonical(self.maintenance_inputs))
        self.run(['node', str(ROOT / 'scripts/environment-actions/maintenance.mjs'), str(source)])

    def tf(self, stack, arguments):
        require(stack == 'identities', 'STACK_REJECTED')
        return self.run(['terraform', f'-chdir={ROOT / "phase4/bootstrap/identities"}', *arguments], timeout=3300)

    def init(self, stack):
        os.environ['TF_WORKSPACE'] = 'default'
        self.tf(stack, ['init', '-input=false', '-reconfigure', '-lockfile=readonly',
                       f'-backend-config=bucket={self.bucket}', '-backend-config=region=us-east-1',
                       '-backend-config=key=phase4/bootstrap/identities/demo-a.tfstate',
                       '-backend-config=workspace_key_prefix=phase4/bootstrap/identities/demo-a-workspaces',
                       '-backend-config=encrypt=true', '-backend-config=use_lockfile=true'])

    def review(self, document):
        source = Path(self.temporary.name) / 'inputs.json'
        source.write_bytes(canonical(self.maintenance_inputs))
        plan_file = Path(self.temporary.name) / 'maintenance-plan.json'
        plan_file.write_bytes(canonical(document))
        self.run(['node', str(ROOT / 'scripts/environment-actions/maintenance.mjs'), str(source), str(plan_file)])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('plan', 'deploy', 'teardown', 'destroy', 'close', 'gate'))
    parser.add_argument('--phase', choices=PHASES, default='infra')
    parser.add_argument('--plan-run', default='')
    parser.add_argument('--hash', default='')
    parser.add_argument('--artifact', default='approved-plan')
    options = parser.parse_args()
    report = Report(options.operation)
    report.data.update(phase=options.phase, account=ACCOUNT, region=REGION, terraform_result='NOT_RUN')
    driver = None
    try:
        with report.phase('authorization-and-contracts', options.operation):
            operation = os.getenv('OPERATION', 'deploy') if options.operation == 'gate' else options.operation
            scope(operation, options.phase)
            config, catalog = configuration()
            if operation in ('plan', 'deploy'):
                technical_inputs(config, options.phase)
            if operation not in ('plan', 'close'):
                protection()
            if operation == 'deploy':
                require(re.fullmatch('[0-9]+', options.plan_run), 'PLAN_RUN_REQUIRED')
                require(re.fullmatch('[0-9a-f]{64}', options.hash), 'APPROVED_PLAN_HASH_REQUIRED')
            if operation in ('plan', 'deploy'):
                artifact_key()
            if options.operation == 'gate':
                return 0
            expected_role = (os.getenv('EXECUTOR_ROLE') if operation == 'close' else
                             role_for(options.phase) if operation == 'plan' and (options.phase in MAINTENANCE_PHASES or options.phase.startswith('recovery-')) else
                             'provision' if operation in ('plan', 'teardown') else 'destroy' if operation == 'destroy' else role_for(options.phase))
            require(os.getenv('EXECUTOR_ROLE') == expected_role, 'EXECUTOR_ROLE_REJECTED')
        driver = (MaintenanceDriver(config, report, options.phase, catalog=catalog) if options.phase in MAINTENANCE_PHASES else
                  RecoveryDriver(config, report, catalog=catalog) if options.phase in ('recovery-closed', 'recovery-expiry') else
                  PipelineDriver(config, report, catalog=catalog))
        with report.phase('identity-backend-inventory', PREFIX):
            driver.load()
            version = json.loads(driver.run(['terraform', 'version', '-json']))
            require(version['terraform_version'] == '1.15.8', 'TERRAFORM_VERSION_REJECTED')
        with report.phase(operation, options.phase):
            if operation == 'plan':
                plan(driver, options.phase)
            elif operation == 'deploy':
                deploy(driver, options.phase, options.plan_run, options.hash, options.artifact)
            elif operation == 'teardown':
                driver.teardown()
            elif operation == 'destroy':
                driver.destroy()
            elif operation == 'close':
                driver.close_endpoint()
    except BaseException as error:
        if isinstance(error, Failure) and error.resource == 'terraform':
            report.data['terraform_result'] = 'FAILED'
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
    sys.exit(main())
