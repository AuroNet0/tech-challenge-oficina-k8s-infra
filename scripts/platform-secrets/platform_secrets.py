import argparse
import base64
import copy
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import secrets
import sys


class ContractError(ValueError):
    pass


def check(condition, code='CONTRACT_REJECTED'):
    if not condition:
        raise ContractError(code)


def parse_json(text):
    def unique_pairs(pairs):
        result = {}
        for name, value in pairs:
            check(name not in result)
            result[name] = value
        return result
    try:
        return json.loads(text, object_pairs_hook=unique_pairs)
    except Exception:
        raise ContractError('INVALID_JSON') from None


@dataclass(frozen=True)
class Scope:
    environment: str
    namespace: str
    account: str
    region: str
    revision: str
    ready: bool

    @classmethod
    def resolve(cls, catalog, environment, account, region, observed_account, observed_region, revision, approval_source='catalog'):
        check(catalog['schema_version'] == 1 and environment in ('demo-a', 'demo-b'))
        settings = catalog['environments'][environment]
        check(re.fullmatch('[0-9a-f]{40}', revision) is not None)
        check(re.fullmatch('[0-9]{12}', account) is not None)
        check(settings['aws_region'] == region == observed_region and account == observed_account, 'IDENTITY_MISMATCH')
        check(settings['aws_account_id'] in (None, account), 'IDENTITY_MISMATCH')
        check(settings['resource_prefix'] == 'tc' and settings['project_name'] == 'oficina')
        check(settings['owner'] == 'tech-challenge-oficina')
        check(type(settings['enabled']) is bool)
        for stack in ('infra', 'platform', 'edge'):
            check(settings['state_keys'][stack] == f'phase4/environments/{environment}/{stack}.tfstate')
        check(approval_source in ('catalog', 'github-environment'), 'APPROVAL_SOURCE_REJECTED')
        authorized = settings['enabled'] or (approval_source == 'github-environment' and environment == 'demo-a'
                                            and account == '446517826146' and region == 'us-east-1')
        ready = authorized and settings['aws_account_id'] == account and bool(re.fullmatch('[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', settings['backend_bucket'] or ''))
        return cls(environment, f'tc-oficina-p4-{environment}', account, region, revision, ready)

    def require_ready(self):
        check(self.ready, 'ENVIRONMENT_DISABLED')


@dataclass(frozen=True)
class SecretSpec:
    name: str
    keys: tuple
    owner: str
    purpose: str
    lifecycle: str
    consumers: tuple
    source: str


def specifications():
    result = [
        SecretSpec('postgres-bootstrap-admin', ('username', 'password'), 'rds', 'bootstrap', 'bootstrap', ('postgres-bootstrap',), 'rds-managed'),
        SecretSpec('auth-migration-postgres', ('username', 'password'), 'auth', 'migration', 'migration', ('postgres-bootstrap', 'auth-migrate'), 'secrets-manager'),
        SecretSpec('mongodb-admin', ('username', 'password'), 'mongodb', 'administration', 'environment', ('mongodb',), 'secrets-manager'),
        SecretSpec('mongodb-keyfile', ('keyfile',), 'mongodb', 'replica-authentication', 'environment', ('mongodb',), 'secrets-manager'),
        SecretSpec('execution-mongodb', ('username', 'password'), 'execution', 'database', 'environment', ('execution-service', 'mongodb'), 'secrets-manager'),
        SecretSpec('rabbitmq-cookie', ('cookie',), 'rabbitmq', 'node-authentication', 'environment', ('rabbitmq',), 'secrets-manager'),
        SecretSpec('rabbitmq-definitions', ('definitions.json',), 'rabbitmq', 'broker-identities', 'environment', ('rabbitmq',), 'derived'),
        SecretSpec('billing-mercado-pago', ('access-token', 'webhook-secret'), 'billing', 'external-provider', 'environment', ('billing-service',), 'external'),
    ]
    for service in ('auth', 'os', 'billing'):
        result.append(SecretSpec(f'{service}-postgres', ('username', 'password'), service, 'database', 'environment',
                                 ('postgres-bootstrap', 'auth-internal' if service == 'auth' else f'{service}-service'), 'secrets-manager'))
    for service in ('os-service', 'execution-service', 'billing-service', 'auth-internal'):
        keys = ('identity.p12', 'truststore.p12', 'password') + (() if service == 'auth-internal' else ('java-options', 'inbound-truststore.p12'))
        result.append(SecretSpec(f'{service}-tls', keys, service, 'mtls', 'certificate', (service,), 'secrets-manager'))
    for service in ('os-service', 'execution-service', 'billing-service'):
        result.append(SecretSpec(f'{service}-rabbitmq', ('username', 'password'), service, 'messaging', 'environment', (service,), 'secrets-manager'))
    return {item.name: item for item in result}


@dataclass(frozen=True)
class Plan:
    scope: Scope
    infra: dict = field(repr=False)
    specs: dict = field(repr=False)
    payment_enabled: bool = False

    @classmethod
    def build(cls, scope, infra, secret_contract, payment_enabled=False):
        check(type(payment_enabled) is bool)
        for key, expected in {'environment_id': scope.environment, 'name_prefix': scope.namespace,
                              'aws_account_id': scope.account, 'aws_region': scope.region}.items():
            check(infra[key] == expected)
        check(infra['eks']['arn'] == f'arn:aws:eks:{scope.region}:{scope.account}:cluster/{scope.namespace}')
        check(infra['rds']['port'] == 5432 and infra['rds']['address'].startswith(f'{scope.namespace}-postgres.')
              and infra['rds']['address'].endswith(f'.{scope.region}.rds.amazonaws.com'))
        check(re.fullmatch(fr'arn:aws:secretsmanager:{scope.region}:{scope.account}:secret:[A-Za-z0-9/_+=.@!-]+-[A-Za-z0-9]{{6}}', infra['rds']['master_secret_arn']) is not None)
        databases = infra['rds']['application_databases']
        check(set(databases) == {'auth', 'os', 'billing'})
        for database in databases.values():
            check(all(re.fullmatch('[a-z][a-z0-9_]{0,53}', database[key]) for key in ('database', 'username')))
        check(len({item['database'] for item in databases.values()}) == 3)
        users = {item['username'] for item in databases.values()}
        check(len(users) == 3 and f"{databases['auth']['username']}_migrator" not in users)
        specs = specifications()
        check(set(specs) == set(secret_contract), 'PLATFORM_CONTRACT_DRIFT')
        for name, spec in specs.items():
            declared = secret_contract[name]
            check(set(declared['keys']) == set(spec.keys) and set(declared['consumers']) == set(spec.consumers), 'PLATFORM_CONTRACT_DRIFT')
            check(declared['temporary'] == (spec.lifecycle in ('bootstrap', 'migration')), 'PLATFORM_CONTRACT_DRIFT')
        check(secret_contract['postgres-bootstrap-admin']['source'] == infra['rds']['master_secret_arn'])
        return cls(scope, copy.deepcopy(infra), specs, payment_enabled)

    def canonical_name(self, name):
        check(name in self.specs or name == 'technical-ca')
        return f'/oficina/phase4/{self.scope.environment}/{name}'

    def labels(self, name):
        spec = self.specs[name]
        return {'oficina/environment': self.scope.environment, 'oficina/managed-by': 'platform-secrets',
                'oficina/owner': spec.owner, 'oficina/purpose': spec.purpose, 'oficina/lifecycle': spec.lifecycle}

    def summary(self):
        return {'mode': 'dry-run', 'environment_id': self.scope.environment, 'namespace': self.scope.namespace,
                'ready': self.scope.ready, 'operations_executed': False,
                'automation': {'repository': 'AuroNet0/tech-challenge-oficina-k8s-infra', 'revision': self.scope.revision,
                               'cluster_arn': self.infra['eks']['arn'], 'oidc_role_required': True, 'oidc_role_created': False}, 'secrets': [
                    {'name': name, 'keys': spec.keys, 'labels': self.labels(name), 'consumers': spec.consumers,
                     'source': self.infra['rds']['master_secret_arn'] if spec.source == 'rds-managed'
                     else 'derived-in-memory' if spec.source == 'derived' else self.canonical_name(name)}
                    for name, spec in self.specs.items()]}


def validate_payload(plan, name, payload):
    spec = plan.specs[name]
    check(isinstance(payload, dict) and set(payload) == set(spec.keys), 'INVALID_SECRET_KEYS')
    check(all(isinstance(value, str) and len(value) <= 800000 for value in payload.values()), 'INVALID_SECRET_FORMAT')
    check(len(json.dumps(payload).encode()) <= 900000, 'SECRET_TOO_LARGE')
    if name.endswith('-postgres') or name == 'postgres-bootstrap-admin':
        if name != 'postgres-bootstrap-admin':
            service = 'auth' if name == 'auth-migration-postgres' else name.split('-')[0]
            username = plan.infra['rds']['application_databases'][service]['username']
            if name == 'auth-migration-postgres':
                username += '_migrator'
            check(payload['username'] == username)
        check(re.fullmatch('[a-z][a-z0-9_]{0,62}', payload['username']) is not None)
        check(re.fullmatch('[!-~]{8,128}' if name == 'postgres-bootstrap-admin' else '[A-Za-z0-9_-]{32,128}', payload['password']) is not None)
    elif name in ('mongodb-admin', 'execution-mongodb') or name.endswith('-rabbitmq'):
        expected = {'mongodb-admin': 'oficina_mongo_admin', 'execution-mongodb': 'oficina_execution'}.get(name, name.removesuffix('-service-rabbitmq'))
        check(payload['username'] == expected)
        check(re.fullmatch('[A-Za-z0-9_-]{32,128}', payload['password']) is not None)
    elif name == 'mongodb-keyfile':
        check(re.fullmatch('[A-Za-z0-9+/]{40,1000}={0,2}', payload['keyfile']) is not None)
    elif name == 'rabbitmq-cookie':
        check(re.fullmatch('[A-Za-z0-9]{32,128}', payload['cookie']) is not None)
    elif name.endswith('-tls'):
        check(re.fullmatch('[A-Za-z0-9_-]{32,128}', payload['password']) is not None)
        for key in [key for key in payload if key.endswith('.p12')]:
            try:
                check(0 < len(base64.b64decode(payload[key], validate=True)) < 512000)
            except Exception:
                raise ContractError('INVALID_SECRET_FORMAT') from None
    elif name == 'billing-mercado-pago':
        check(all(not value or (16 <= len(value) <= 512 and not any(character.isspace() for character in value)) for value in payload.values()))
        check(not plan.payment_enabled or all(payload.values()), 'EXTERNAL_SOURCE_MISSING')
    elif name == 'rabbitmq-definitions':
        definitions = parse_json(payload['definitions.json'])
        check(set(definitions) == {'users', 'vhosts', 'permissions'})
        check(definitions['vhosts'] == [{'name': '/oficina'}])
        check(len(definitions['users']) == len(definitions['permissions']) == 3)


def rabbit_definitions(credentials):
    users = []
    permissions = []
    for service in ('os', 'execution', 'billing'):
        credential = credentials[f'{service}-service-rabbitmq']
        salt = hashlib.sha256(('rabbit-salt:' + credential['username'] + credential['password']).encode()).digest()[:4]
        password_hash = base64.b64encode(salt + hashlib.sha256(salt + credential['password'].encode()).digest()).decode()
        users.append({'name': credential['username'], 'password_hash': password_hash, 'hashing_algorithm': 'rabbit_password_hashing_sha256', 'tags': []})
        configure = r'^(oficina\..*|execution\.diagnostic\.start|os\.saga\.events(\.dlq)?)$' if service == 'os' else rf'^(oficina\..*|{service}\..*)$'
        readable = r'^os\.saga\.events(\.dlq)?$' if service == 'os' else rf'^{service}\..*$'
        permissions.append({'user': credential['username'], 'vhost': '/oficina', 'configure': configure, 'write': configure, 'read': readable})
    return {'definitions.json': json.dumps({'users': users, 'vhosts': [{'name': '/oficina'}], 'permissions': permissions}, separators=(',', ':'))}


def validate_canonical(plan, name, payload):
    check(len(json.dumps(payload).encode()) <= 65536, 'CANONICAL_SECRET_TOO_LARGE')
    if name.endswith('-tls'):
        check(set(payload) == {'identity.p12', 'password'})
        check(re.fullmatch('[A-Za-z0-9_-]{32,128}', payload['password']) is not None)
        try:
            check(0 < len(base64.b64decode(payload['identity.p12'], validate=True)) < 45000)
        except Exception:
            raise ContractError('INVALID_SECRET_FORMAT') from None
    elif name in plan.specs:
        validate_payload(plan, name, payload)
    else:
        check(name == 'technical-ca' and set(payload) == {'ca.p12', 'password'})


def equivalent_payload(name, current, expected):
    if not name.endswith('-tls'):
        return current == expected
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.serialization import pkcs12

        derived = {'truststore.p12', 'inbound-truststore.p12'}
        if set(current) != set(expected) or any(current[key] != expected[key] for key in expected if key not in derived):
            return False
        for key in derived & set(expected):
            stores = [pkcs12.load_pkcs12(base64.b64decode(payload[key], validate=True), payload['password'].encode()) for payload in (current, expected)]
            if any(store.key is not None or store.cert is not None for store in stores):
                return False
            if {item.certificate.fingerprint(hashes.SHA256()) for item in stores[0].additional_certs} != {item.certificate.fingerprint(hashes.SHA256()) for item in stores[1].additional_certs}:
                return False
        return True
    except Exception:
        return False


class MemorySource:
    def __init__(self):
        self.records = {}

    def contains(self, plan, name):
        plan.scope.require_ready()
        return plan.canonical_name(name) in self.records

    def put_once(self, plan, name, payload):
        plan.scope.require_ready()
        validate_canonical(plan, name, payload)
        canonical = plan.canonical_name(name)
        if canonical in self.records:
            record = self.read(plan, name)
            check(record['payload'] == payload, 'ROTATION_REQUIRED')
            return
        self.records[canonical] = {'environment': plan.scope.environment, 'name': canonical, 'owner': 'platform-secrets',
                                   'version': secrets.token_hex(16), 'payload': copy.deepcopy(payload)}

    def read(self, plan, name):
        plan.scope.require_ready()
        canonical = plan.canonical_name(name)
        check(canonical in self.records, 'SOURCE_MISSING')
        record = self.records[canonical]
        check(record['name'] == canonical and record['environment'] == plan.scope.environment and record['owner'] == 'platform-secrets', 'SOURCE_OWNERSHIP_MISMATCH')
        return copy.deepcopy(record)


class MemoryTarget:
    def __init__(self):
        self.records = {}

    def deliver(self, plan, name, payload):
        plan.scope.require_ready()
        validate_payload(plan, name, payload)
        identity = (plan.scope.namespace, name)
        labels = plan.labels(name)
        if identity in self.records:
            current = self.records[identity]
            check(current['metadata']['labels'] == labels, 'TARGET_OWNERSHIP_MISMATCH')
            check(equivalent_payload(name, current['data'], payload), 'ROTATION_REQUIRED')
            return current['metadata']['uid']
        uid = secrets.token_hex(16)
        self.records[identity] = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
                                  'metadata': {'name': name, 'namespace': plan.scope.namespace, 'labels': labels, 'uid': uid, 'resourceVersion': '1'},
                                  'data': copy.deepcopy(payload)}
        return uid

    def verify(self, plan, name, payload):
        record = self.records.get((plan.scope.namespace, name))
        check(record is not None and record['metadata']['labels'] == plan.labels(name) and equivalent_payload(name, record['data'], payload), 'DELIVERY_NOT_VERIFIED')

    def remove_temporary(self, plan, lifecycle, expected_uids, completed):
        plan.scope.require_ready()
        check(lifecycle in ('bootstrap', 'migration') and completed, 'CLEANUP_PRECONDITION_FAILED')
        names = [name for name, spec in plan.specs.items() if spec.lifecycle == lifecycle]
        for name in names:
            identity = (plan.scope.namespace, name)
            record = self.records.get(identity)
            if record is not None:
                check(record['metadata']['labels'] == plan.labels(name) and record['metadata']['uid'] == expected_uids.get(name), 'TARGET_OWNERSHIP_MISMATCH')
        for name in names:
            self.records.pop((plan.scope.namespace, name), None)


@dataclass(repr=False)
class PreparedBundle:
    payloads: dict
    ca_certificate: object
    public_roots: tuple


def prepare(plan, source, master, public_roots, external, now=None):
    from pki import compose_identity, create_ca, issue_identity, validate_identity
    from cryptography.hazmat.primitives.serialization import pkcs12

    plan.scope.require_ready()
    validate_payload(plan, 'postgres-bootstrap-admin', master)
    check(len(public_roots) > 0, 'PUBLIC_ROOTS_MISSING')
    check('billing-mercado-pago' in external, 'EXTERNAL_SOURCE_MISSING')
    validate_payload(plan, 'billing-mercado-pago', external['billing-mercado-pago'])
    if not source.contains(plan, 'technical-ca'):
        password = secrets.token_urlsafe(36)
        ca_bytes = create_ca(plan.scope.environment, password, now=now)
        source.put_once(plan, 'technical-ca', {'ca.p12': base64.b64encode(ca_bytes).decode(), 'password': password})
    ca_payload = source.read(plan, 'technical-ca')['payload']
    ca_bytes = base64.b64decode(ca_payload['ca.p12'], validate=True)
    _, ca_certificate, _ = pkcs12.load_key_and_certificates(ca_bytes, ca_payload['password'].encode())
    payloads = {}
    for name, spec in plan.specs.items():
        if spec.source == 'derived':
            continue
        if spec.source == 'rds-managed':
            payload = master
        elif source.contains(plan, name):
            payload = source.read(plan, name)['payload']
            if name.endswith('-tls'):
                validate_canonical(plan, name, payload)
                payload = compose_identity(payload, ca_certificate, name.removesuffix('-tls'), public_roots)
        elif spec.source == 'external':
            check(name in external, 'EXTERNAL_SOURCE_MISSING')
            payload = external[name]
        elif name.endswith('-tls'):
            payload = issue_identity(ca_bytes, ca_payload['password'], name.removesuffix('-tls'), plan.scope.namespace, secrets.token_urlsafe(36), public_roots, now=now)
        elif name == 'mongodb-keyfile':
            payload = {'keyfile': base64.b64encode(secrets.token_bytes(512)).decode()}
        elif name == 'rabbitmq-cookie':
            payload = {'cookie': secrets.token_hex(32)}
        else:
            username = {'mongodb-admin': 'oficina_mongo_admin', 'execution-mongodb': 'oficina_execution'}.get(name)
            if name.endswith('-postgres'):
                service = 'auth' if name == 'auth-migration-postgres' else name.split('-')[0]
                username = plan.infra['rds']['application_databases'][service]['username'] + ('_migrator' if name == 'auth-migration-postgres' else '')
            if name.endswith('-rabbitmq'):
                username = name.removesuffix('-service-rabbitmq')
            payload = {'username': username, 'password': secrets.token_urlsafe(36)}
        validate_payload(plan, name, payload)
        if spec.purpose == 'mtls':
            validate_identity(payload, plan.scope.environment, name.removesuffix('-tls'), plan.scope.namespace, ca_certificate, public_roots, now=now)
        if spec.source != 'rds-managed':
            canonical = {key: payload[key] for key in ('identity.p12', 'password')} if name.endswith('-tls') else payload
            source.put_once(plan, name, canonical)
        payloads[name] = payload
    payloads['rabbitmq-definitions'] = rabbit_definitions(payloads)
    return PreparedBundle(payloads, ca_certificate, tuple(public_roots))


def deliver(plan, target, bundle):
    from pki import validate_identity

    payloads = bundle.payloads
    check(set(payloads) == set(plan.specs))
    for name, payload in payloads.items():
        validate_payload(plan, name, payload)
        if name.endswith('-tls'):
            validate_identity(payload, plan.scope.environment, name.removesuffix('-tls'), plan.scope.namespace, bundle.ca_certificate, bundle.public_roots)
    check(payloads['rabbitmq-definitions'] == rabbit_definitions(payloads), 'BROKER_CREDENTIAL_MISMATCH')
    receipts = {}
    for name, payload in payloads.items():
        receipts[name] = target.deliver(plan, name, payload)
        target.verify(plan, name, payload)
    return receipts


def release_ready(plan, evidence, expected_job, expected_image):
    check(evidence['namespace'] == plan.scope.namespace and evidence['environment'] == plan.scope.environment)
    check(evidence['databases'] == plan.infra['rds']['application_databases'])
    check(evidence['auth_owner'] == plan.infra['rds']['application_databases']['auth']['username'] + '_migrator')
    check(evidence['grants_verified'] is True and evidence['runtime_ddl_denied'] is True)
    job = evidence['migration']
    check(job['name'] == expected_job and job['image'] == expected_image and bool(job['uid']))
    check(job['complete'] is True and job['failed'] is False, 'MIGRATION_INCOMPLETE')
    check(expected_job.startswith(f'auth-migrate-{plan.scope.revision[:12]}-'))
    check(re.fullmatch(fr'{plan.scope.account}\.dkr\.ecr\.{plan.scope.region}\.amazonaws\.com/{plan.scope.namespace}-auth@sha256:[0-9a-f]{{64}}', expected_image) is not None)
    check(expected_job.endswith(expected_image.split('@sha256:')[1][:12]))
    return True


def main():
    parser = argparse.ArgumentParser(description='Metadata-only Platform dry-run. No network transport is enabled.')
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--contract', required=True)
    parser.add_argument('--environment', required=True, choices=('demo-a', 'demo-b'))
    parser.add_argument('--account', required=True)
    parser.add_argument('--region', required=True)
    parser.add_argument('--revision', required=True)
    arguments = parser.parse_args()
    try:
        catalog = parse_json(Path(arguments.catalog).read_text(encoding='utf-8-sig'))
        contract = parse_json(Path(arguments.contract).read_text(encoding='utf-8-sig'))
        scope = Scope.resolve(catalog, arguments.environment, arguments.account, arguments.region, arguments.account, arguments.region, arguments.revision)
        plan = Plan.build(scope, contract['infra_contract'], contract['secret_contract'], contract['secret_delivery_contract']['mercado_pago_enabled'])
        print(json.dumps(plan.summary()))
    except Exception:
        print('PLATFORM_SECRETS_REJECTED', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
