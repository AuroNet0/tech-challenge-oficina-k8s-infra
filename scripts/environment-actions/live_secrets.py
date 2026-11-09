import json
import re
from pathlib import Path

from runtime import ACCOUNT, PREFIX, REGION, ROOT, Failure, module_at, require


def secret_context(driver):
    contracts = module_at('platform_secrets', 'scripts/platform-secrets', 'platform_secrets.py')
    transports = module_at('secret_transports', 'scripts/platform-secrets', 'transports.py')
    catalog = json.loads((ROOT / 'phase4/environments/catalog.json').read_text())
    infra = driver.outputs('infra')['platform_contract']
    platform = driver.outputs('platform')
    import os
    scope = contracts.Scope.resolve(catalog, 'demo-a', ACCOUNT, REGION, ACCOUNT, REGION, os.environ['GITHUB_SHA'])
    plan = contracts.Plan.build(scope, infra, platform['secret_contract'], payment_enabled=True)

    class DurableStore(transports.SecretsManagerStore):
        def contains(self, plan, name):
            if name not in self.inventory:
                try:
                    observed = driver.api('secretsmanager', 'describe_secret', SecretId=plan.canonical_name(name))
                except Failure as error:
                    if error.code != 'ResourceNotFoundException':
                        raise
                else:
                    transports.validate_arn(plan, name, observed['ARN'])
                    expected = transports.canonical_tags(plan, name)
                    actual = {tag['Key']: tag['Value'] for tag in observed.get('Tags', [])}
                    require(all(actual.get(key) == value for key, value in expected.items()), 'SECRET_OWNERSHIP_REJECTED')
                    self.inventory[name] = observed['ARN']
                    driver.journal['secrets'][name] = observed['ARN']
                    driver.save()
            return super().contains(plan, name)

        def put_once(self, plan, name, payload):
            try:
                return super().put_once(plan, name, payload)
            finally:
                driver.journal['secrets'].update({key: value for key, value in self.inventory.items() if key != 'postgres-bootstrap-admin'})
                driver.save()

    class ReportedSecretsClient:
        @property
        def meta(self):
            return driver.client('secretsmanager').meta

        def __getattr__(self, operation):
            def invoke(**arguments):
                resource = arguments.get('SecretId', arguments.get('Name', 'canonical-source'))
                with driver.report.phase('secretsmanager-' + operation, resource):
                    return driver.api('secretsmanager', operation, **arguments)
            return invoke

    inventory = dict(driver.journal['secrets'], **{'postgres-bootstrap-admin': infra['rds']['master_secret_arn']})
    source = DurableStore(ReportedSecretsClient(), inventory,
        lambda: driver.api('sts', 'get_caller_identity'),
        lambda name: driver.api('rds', 'describe_db_instances', DBInstanceIdentifier=name)['DBInstances'][0])
    from kubernetes import client, config
    config.load_kube_config()
    class ReportedKubernetesApi(transports.KubernetesApi):
        def create_secret(self, namespace, body):
            with driver.report.phase('project-secret', namespace + '/' + body['metadata']['name']):
                try:
                    return super().create_secret(namespace, body)
                except Exception as error:
                    raise Failure('KUBERNETES_SECRET_API_FAILED', namespace,
                        dict(http_status=getattr(error, 'status', None))) from None

    api = ReportedKubernetesApi(client.CoreV1Api(), client.ApiClient())

    def connection():
        actual = driver.cluster()
        return dict(arn=actual['arn'], endpoint=actual['endpoint'], certificate_authority_data=actual['certificateAuthority']['data'])

    target = transports.KubernetesTarget(api, api.read_namespace, connection)
    return contracts, plan, source, target


def public_roots(driver):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from live_release import registry_login
    reference_roots = None
    with registry_login(driver) as docker:
        for image in driver.journal['images'].values():
            driver.run([*docker, 'pull', image['reference']])
            output = driver.run([*docker, 'run', '--rm', '--network=none', '--read-only', '--entrypoint', 'keytool',
                image['reference'], '-list', '-rfc', '-cacerts', '-storepass', 'changeit'])
            certificates = x509.load_pem_x509_certificates(output.encode())
            current = {certificate.fingerprint(hashes.SHA256()): certificate for certificate in certificates}
            require(current, 'PRODUCTION_PUBLIC_ROOTS_MISSING')
            if reference_roots is not None:
                require(set(current) == set(reference_roots), 'JRE_PUBLIC_ROOTS_DIFFER_BETWEEN_SERVICES')
            reference_roots = current
    return tuple(reference_roots.values())


def deliver_secrets(driver):
    contracts, plan, source, target = secret_context(driver)
    master = source.read(plan, 'postgres-bootstrap-admin')['payload']
    external = json.loads(driver.api('secretsmanager', 'get_secret_value',
        SecretId=driver.config['mercado_pago_test_secret_arn'])['SecretString'])
    require(external.get('access-token', '').startswith('TEST-'), 'MERCADO_PAGO_TEST_CREDENTIAL_REQUIRED')
    bundle = contracts.prepare(plan, source, master, public_roots(driver), {'billing-mercado-pago': external})
    receipts = contracts.deliver(plan, target, bundle)
    driver.receipt('secrets', dict(verified=True, uids=receipts))


def cleanup_temporary(driver, lifecycle):
    contracts, plan, source, target = secret_context(driver)
    receipts = driver.journal['receipts'].get('secrets', {}).get('uids', {})
    completed = driver.journal['receipts'].get('bootstrap' if lifecycle == 'bootstrap' else 'migration', {}).get('verified') is True
    target.remove_temporary(plan, lifecycle, receipts, completed)


def cleanup_secrets(driver):
    contracts = module_at('cleanup_secret_contract', 'scripts/platform-secrets', 'platform_secrets.py')
    names = {name for name, spec in contracts.specifications().items() if spec.source in ('secrets-manager', 'external')} | {'technical-ca'}
    for name, arn in driver.journal['secrets'].items():
        require(name in names and re.fullmatch(fr'arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:/oficina/phase4/demo-a/{re.escape(name)}-[A-Za-z0-9]{{6}}', arn), 'SECRET_DESTROY_SCOPE_REJECTED')
    for name in sorted(names):
        if name in driver.journal['secrets']:
            continue
        try:
            observed = driver.api('secretsmanager', 'describe_secret', SecretId='/oficina/phase4/demo-a/' + name)
        except Failure as error:
            if error.code == 'ResourceNotFoundException':
                continue
            raise
        tags = {tag['Key']: tag['Value'] for tag in observed.get('Tags', [])}
        require(tags.get('EnvironmentId') == 'demo-a' and tags.get('ManagedBy') == 'platform-secrets'
            and tags.get('Lifecycle') == 'ephemeral', 'SECRET_DESTROY_OWNERSHIP_REJECTED')
        driver.journal['secrets'][name] = observed['ARN']
        driver.save()
    for name, arn in driver.journal['secrets'].items():
        require(name != 'auth-signing' and arn.startswith(f'arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:/oficina/phase4/demo-a/{name}-'), 'SECRET_DESTROY_SCOPE_REJECTED')
        try:
            observed = driver.api('secretsmanager', 'describe_secret', SecretId=arn)
        except Failure as error:
            if error.code == 'ResourceNotFoundException':
                continue
            raise
        tags = {tag['Key']: tag['Value'] for tag in observed.get('Tags', [])}
        require(observed['ARN'] == arn and tags.get('EnvironmentId') == 'demo-a' and tags.get('Lifecycle') == 'ephemeral'
            and tags.get('ManagedBy') == 'platform-secrets', 'SECRET_DESTROY_OWNERSHIP_REJECTED')
        if not observed.get('DeletedDate'):
            driver.api('secretsmanager', 'delete_secret', SecretId=arn, ForceDeleteWithoutRecovery=True)
