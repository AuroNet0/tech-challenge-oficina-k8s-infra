import base64
import copy
from functools import wraps
import json
import re
import uuid

from platform_secrets import check, equivalent_payload, parse_json, validate_canonical, validate_payload


def sanitized_transport(operation):
    @wraps(operation)
    def execute(*arguments, **keywords):
        from platform_secrets import ContractError
        try:
            return operation(*arguments, **keywords)
        except ContractError:
            raise
        except Exception:
            raise ContractError('TRANSPORT_REJECTED') from None
    return execute


def validate_arn(plan, name, arn):
    check(name in plan.specs or name == 'technical-ca')
    if name == 'postgres-bootstrap-admin':
        check(arn == plan.infra['rds']['master_secret_arn'], 'SOURCE_ARN_MISMATCH')
    else:
        check(re.fullmatch(fr'arn:aws:secretsmanager:{plan.scope.region}:{plan.scope.account}:secret:{re.escape(plan.canonical_name(name))}-[A-Za-z0-9]{{6}}', arn) is not None, 'SOURCE_ARN_MISMATCH')


def canonical_tags(plan, name):
    spec = plan.specs.get(name)
    return {'EnvironmentId': plan.scope.environment, 'ManagedBy': 'platform-secrets', 'Owner': spec.owner if spec else 'pki',
            'Purpose': spec.purpose if spec else 'technical-ca', 'Lifecycle': 'ephemeral',
            'ProjectionLifecycle': spec.lifecycle if spec else 'certificate', 'Origin': spec.source if spec else 'generated-pki',
            'Consumers': ','.join(spec.consumers) if spec else 'automation-only'}


class SecretsManagerReader:
    def __init__(self, client, inventory, identity_reader, rds_reader=None):
        self.client = client
        self.inventory = copy.deepcopy(inventory)
        self.identity_reader = identity_reader
        self.rds_reader = rds_reader

    def _context(self, plan):
        plan.scope.require_ready()
        check(self.identity_reader()['Account'] == plan.scope.account, 'IDENTITY_MISMATCH')
        check(self.client.meta.region_name == plan.scope.region, 'IDENTITY_MISMATCH')

    def contains(self, plan, name):
        plan.scope.require_ready()
        if name in self.inventory:
            validate_arn(plan, name, self.inventory[name])
            return True
        return False

    @sanitized_transport
    def read(self, plan, name):
        self._context(plan)
        check(name in self.inventory, 'SOURCE_MISSING')
        arn = self.inventory[name]
        validate_arn(plan, name, arn)
        try:
            description = self.client.describe_secret(SecretId=arn)
            check(description['ARN'] == arn and not description.get('DeletedDate'), 'SOURCE_OWNERSHIP_MISMATCH')
            if name == 'postgres-bootstrap-admin':
                check(self.rds_reader is not None, 'RDS_SOURCE_NOT_VERIFIED')
                identifier = f'{plan.scope.namespace}-postgres'
                database = self.rds_reader(identifier)
                check(database['DBInstanceIdentifier'] == identifier)
                check(database['DBInstanceArn'] == f'arn:aws:rds:{plan.scope.region}:{plan.scope.account}:db:{identifier}')
                check(database['MasterUserSecret']['SecretArn'] == arn)
                check(database['Endpoint']['Address'] == plan.infra['rds']['address'] and database['Endpoint']['Port'] == 5432)
                check(database['Engine'] == 'postgres' and database['EngineVersion'].split('.')[0] == '17')
            if name != 'postgres-bootstrap-admin':
                check(description['Name'] == plan.canonical_name(name), 'SOURCE_OWNERSHIP_MISMATCH')
                tags = {item['Key']: item['Value'] for item in description.get('Tags', [])}
                check(all(tags.get(key) == value for key, value in canonical_tags(plan, name).items()), 'SOURCE_OWNERSHIP_MISMATCH')
            response = self.client.get_secret_value(SecretId=arn, VersionStage='AWSCURRENT')
            check(response['ARN'] == arn and bool(response['VersionId']))
            payload = parse_json(response['SecretString'])
            if name == 'postgres-bootstrap-admin':
                payload = {key: payload[key] for key in ('username', 'password')}
            return {'version': response['VersionId'], 'payload': payload}
        except Exception:
            from platform_secrets import ContractError
            raise ContractError('SOURCE_READ_REJECTED') from None


class SecretsManagerStore(SecretsManagerReader):
    @sanitized_transport
    def put_once(self, plan, name, payload):
        from platform_secrets import ContractError

        self._context(plan)
        check(name != 'postgres-bootstrap-admin' and (name == 'technical-ca' or plan.specs[name].source in ('secrets-manager', 'external')))
        validate_canonical(plan, name, payload)
        if self.contains(plan, name):
            check(self.read(plan, name)['payload'] == payload, 'ROTATION_REQUIRED')
            return
        tags = [{'Key': key, 'Value': value} for key, value in canonical_tags(plan, name).items()]
        try:
            response = self.client.create_secret(Name=plan.canonical_name(name), SecretString=json.dumps(payload),
                                                 ClientRequestToken=str(uuid.uuid4()), Tags=tags)
            validate_arn(plan, name, response['ARN'])
            self.inventory[name] = response['ARN']
            check(self.read(plan, name)['payload'] == payload, 'SOURCE_NOT_VERIFIED')
        except Exception:
            raise ContractError('SOURCE_CREATE_REJECTED') from None


class KubernetesTarget:
    def __init__(self, api, namespace_reader, connection_reader):
        self.api = api
        self.namespace_reader = namespace_reader
        self.connection_reader = connection_reader

    def _namespace(self, plan):
        plan.scope.require_ready()
        connection = self.connection_reader()
        check(all(connection[key] == plan.infra['eks'][key] for key in ('arn', 'endpoint', 'certificate_authority_data')), 'CLUSTER_MISMATCH')
        namespace = self.namespace_reader(plan.scope.namespace)
        check(namespace['metadata']['name'] == plan.scope.namespace)
        labels = namespace['metadata']['labels']
        check(labels.get('oficina/environment') == plan.scope.environment and labels.get('oficina/managed-by') == 'platform', 'NAMESPACE_OWNERSHIP_MISMATCH')

    def _read(self, plan, name):
        return self.api.read_secret(plan.scope.namespace, name)

    def _equivalent(self, name, current, payload):
        try:
            decoded = {key: value if key.endswith('.p12') else base64.b64decode(value, validate=True).decode() for key, value in current.items()}
            return equivalent_payload(name, decoded, payload)
        except Exception:
            return False

    @sanitized_transport
    def deliver(self, plan, name, payload):
        self._namespace(plan)
        check(name in plan.specs)
        validate_payload(plan, name, payload)
        current = self._read(plan, name)
        data = {key: (value if key.endswith('.p12') else base64.b64encode(value.encode()).decode()) for key, value in payload.items()}
        if current is not None:
            check(current['metadata']['namespace'] == plan.scope.namespace and current['metadata']['name'] == name)
            check(current['metadata']['labels'] == plan.labels(name), 'TARGET_OWNERSHIP_MISMATCH')
            check(current['type'] == 'Opaque' and self._equivalent(name, current['data'], payload), 'ROTATION_REQUIRED')
            return current['metadata']['uid']
        body = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
                'metadata': {'namespace': plan.scope.namespace, 'name': name, 'labels': plan.labels(name)}, 'data': data}
        self.api.create_secret(plan.scope.namespace, body)
        current = self._read(plan, name)
        check(current is not None and current['data'] == data and current['metadata']['labels'] == plan.labels(name), 'DELIVERY_NOT_VERIFIED')
        return current['metadata']['uid']

    @sanitized_transport
    def verify(self, plan, name, payload):
        self._namespace(plan)
        record = self._read(plan, name)
        check(record is not None and self._equivalent(name, record['data'], payload) and record['metadata']['labels'] == plan.labels(name), 'DELIVERY_NOT_VERIFIED')

    @sanitized_transport
    def remove_temporary(self, plan, lifecycle, expected_uids, completed):
        self._namespace(plan)
        check(lifecycle in ('bootstrap', 'migration') and completed, 'CLEANUP_PRECONDITION_FAILED')
        pending = []
        for name, spec in plan.specs.items():
            if spec.lifecycle != lifecycle:
                continue
            current = self._read(plan, name)
            if current is None:
                continue
            metadata = current['metadata']
            check(metadata['namespace'] == plan.scope.namespace and metadata['name'] == name)
            check(metadata['labels'] == plan.labels(name) and metadata['uid'] == expected_uids.get(name), 'TARGET_OWNERSHIP_MISMATCH')
            pending.append((name, metadata['uid'], metadata['resourceVersion']))
        for name, uid, version in pending:
            self.api.delete_secret(plan.scope.namespace, name, {'uid': uid, 'resourceVersion': version})
            check(self._read(plan, name) is None, 'CLEANUP_NOT_VERIFIED')


class KubernetesApi:
    def __init__(self, core_api, api_client):
        self.core_api = core_api
        self.api_client = api_client

    def read_namespace(self, namespace):
        return self.api_client.sanitize_for_serialization(self.core_api.read_namespace(namespace))

    def read_secret(self, namespace, name):
        try:
            return self.api_client.sanitize_for_serialization(self.core_api.read_namespaced_secret(name, namespace))
        except Exception as failure:
            if getattr(failure, 'status', None) == 404:
                return None
            raise

    def create_secret(self, namespace, body):
        self.core_api.create_namespaced_secret(namespace, body)

    def delete_secret(self, namespace, name, preconditions):
        self.core_api.delete_namespaced_secret(name, namespace, body={
            'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': preconditions})


def destroy_inventory(plan, inventory, confirmation, dependents_removed):
    plan.scope.require_ready()
    check(confirmation == f'DESTROY {plan.scope.environment} {plan.scope.account} {plan.scope.namespace}' and dependents_removed is True, 'DESTROY_PRECONDITION_FAILED')
    names = {name for name, spec in plan.specs.items() if spec.source in ('secrets-manager', 'external')} | {'technical-ca'}
    check(set(inventory) == names, 'DESTROY_INVENTORY_MISMATCH')
    for name, arn in inventory.items():
        validate_arn(plan, name, arn)
    return [{'name': name, 'arn': inventory[name], 'action': 'review-scheduled-deletion'} for name in sorted(names)]
