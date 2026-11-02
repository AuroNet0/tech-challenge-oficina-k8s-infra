import base64
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace
import unittest

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from pki import InvalidMaterial, create_ca, issue_identity, validate_identity
from platform_secrets import ContractError, MemorySource, MemoryTarget, Plan, Scope, check, deliver, equivalent_payload, parse_json, prepare, release_ready, specifications, validate_payload
from transports import KubernetesTarget, SecretsManagerReader, SecretsManagerStore, destroy_inventory, validate_arn
from check_secret_files import unsafe_file


ROOT = Path(__file__).resolve().parents[2]
REVISION = '33fb5197ea83954412948e9b3cc248fbb8d5df5a'
ACCOUNT = '123456789012'


def fixture(environment='demo-a', enabled=True):
    catalog = json.loads((ROOT / 'phase4/environments/catalog.json').read_text())
    if enabled:
        catalog['environments'][environment].update(enabled=True, aws_account_id=ACCOUNT, backend_bucket='synthetic-offline-tests')
    scope = Scope.resolve(catalog, environment, ACCOUNT, 'us-east-1', ACCOUNT, 'us-east-1', REVISION)
    infra = json.loads((ROOT / 'phase4/stacks/platform/tests/fixture.json').read_text())
    infra.update(environment_id=environment, name_prefix=scope.namespace)
    infra['rds']['address'] = infra['rds']['address'].replace('demo-a', environment)
    infra['eks']['arn'] = f'arn:aws:eks:us-east-1:{ACCOUNT}:cluster/{scope.namespace}'
    infra['rds']['master_secret_arn'] = f'arn:aws:secretsmanager:us-east-1:{ACCOUNT}:secret:rds!db-{environment}-ABC123'
    contract = {name: {'keys': spec.keys, 'consumers': spec.consumers, 'temporary': spec.lifecycle in ('bootstrap', 'migration'), 'source': spec.source}
                for name, spec in specifications().items()}
    contract['postgres-bootstrap-admin']['source'] = infra['rds']['master_secret_arn']
    return Plan.build(scope, infra, contract)


class FakeSecretsManager:
    def __init__(self):
        self.records = {}
        self.calls = []
        self.meta = SimpleNamespace(region_name='us-east-1')

    def create_secret(self, **arguments):
        self.calls.append('create')
        arn = f"arn:aws:secretsmanager:us-east-1:{ACCOUNT}:secret:{arguments['Name']}-ABC123"
        check(arn not in self.records, 'ALREADY_EXISTS')
        self.records[arn] = {'ARN': arn, 'Name': arguments['Name'], 'Tags': arguments['Tags'],
                             'SecretString': arguments['SecretString'], 'VersionId': arguments['ClientRequestToken']}
        return {'ARN': arn}

    def describe_secret(self, SecretId):
        self.calls.append('describe')
        return {key: self.records[SecretId][key] for key in ('ARN', 'Name', 'Tags')}

    def get_secret_value(self, SecretId, VersionStage):
        self.calls.append('get')
        check(VersionStage == 'AWSCURRENT')
        return {key: self.records[SecretId][key] for key in ('ARN', 'SecretString', 'VersionId')}


class FakeKubernetes:
    def __init__(self):
        self.records = {}
        self.deletions = []

    def read_secret(self, namespace, name):
        return copy.deepcopy(self.records.get((namespace, name)))

    def create_secret(self, namespace, body):
        identity = (namespace, body['metadata']['name'])
        check(identity not in self.records)
        body = copy.deepcopy(body)
        body['metadata'].update(uid=f'uid-{len(self.records)}', resourceVersion='1')
        self.records[identity] = body

    def delete_secret(self, namespace, name, preconditions):
        current = self.records[(namespace, name)]
        check(all(current['metadata'][key] == value for key, value in preconditions.items()), 'CONFLICT')
        self.deletions.append((namespace, name))
        del self.records[(namespace, name)]


class SecretsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = fixture()
        cls.source = MemorySource()
        password = 'synthetic-password-only-' + 'A' * 32
        public_ca = create_ca('demo-b', password)
        _, cls.public_root, _ = pkcs12.load_key_and_certificates(public_ca, password.encode())
        cls.master = {'username': 'oficina_admin', 'password': 'synthetic-master-only-' + 'M' * 32}
        cls.external = {'billing-mercado-pago': {'access-token': '', 'webhook-secret': ''}}
        cls.bundle = prepare(cls.plan, cls.source, cls.master, [cls.public_root], cls.external)

    def test_both_environments_resolve_and_disabled_rejects_mutation(self):
        for environment in ('demo-a', 'demo-b'):
            plan = fixture(environment, enabled=False)
            self.assertFalse(plan.scope.ready)
            self.assertEqual(plan.scope.namespace, f'tc-oficina-p4-{environment}')
            with self.assertRaisesRegex(ContractError, 'ENVIRONMENT_DISABLED'):
                prepare(plan, MemorySource(), self.master, [self.public_root], self.external)

    def test_identity_mismatch(self):
        catalog = json.loads((ROOT / 'phase4/environments/catalog.json').read_text())
        for observed_account, observed_region in (('000000000000', 'us-east-1'), (ACCOUNT, 'sa-east-1')):
            with self.assertRaisesRegex(ContractError, 'IDENTITY_MISMATCH'):
                Scope.resolve(catalog, 'demo-a', ACCOUNT, 'us-east-1', observed_account, observed_region, REVISION)

    def test_arbitrary_environment_rejected(self):
        catalog = json.loads((ROOT / 'phase4/environments/catalog.json').read_text())
        for name in ('../demo-a', 'production', 'demo-a/../../demo-b', 'DEMO-A'):
            with self.assertRaises(ContractError):
                Scope.resolve(catalog, name, ACCOUNT, 'us-east-1', ACCOUNT, 'us-east-1', REVISION)

    def test_contract_drift_rejected(self):
        with self.assertRaisesRegex(ContractError, 'PLATFORM_CONTRACT_DRIFT'):
            Plan.build(self.plan.scope, self.plan.infra, {})

    def test_missing_keys_and_formats(self):
        for payload in ({'username': 'oficina_auth'}, {'username': 'other', 'password': 'A' * 48},
                        {'username': 'oficina_auth', 'password': 'short'}, {'username': 'oficina_auth', 'password': 'A' * 48, 'extra': 'forbidden'}):
            with self.assertRaises(ContractError):
                validate_payload(self.plan, 'auth-postgres', payload)

    def test_source_cross_environment_and_ownership(self):
        other = fixture('demo-b')
        with self.assertRaisesRegex(ContractError, 'SOURCE_MISSING'):
            self.source.read(other, 'auth-postgres')
        source = copy.deepcopy(self.source)
        source.records[self.plan.canonical_name('auth-postgres')]['owner'] = 'unexpected'
        with self.assertRaisesRegex(ContractError, 'SOURCE_OWNERSHIP_MISMATCH'):
            source.read(self.plan, 'auth-postgres')

    def test_sdk_arn_and_ownership_before_read(self):
        client = FakeSecretsManager()
        store = SecretsManagerStore(client, {}, lambda: {'Account': ACCOUNT})
        store.put_once(self.plan, 'auth-postgres', self.bundle.payloads['auth-postgres'])
        store.put_once(self.plan, 'auth-postgres', self.bundle.payloads['auth-postgres'])
        self.assertEqual(client.calls.count('create'), 1)
        arn = store.inventory['auth-postgres']
        for other in (arn.replace('demo-a', 'demo-b'), arn.replace(ACCOUNT, '000000000000'), arn.replace('us-east-1', 'sa-east-1')):
            with self.assertRaises(ContractError):
                SecretsManagerReader(client, {'auth-postgres': other}, lambda: {'Account': ACCOUNT}).read(self.plan, 'auth-postgres')
        client.records[arn]['Tags'] = []
        gets = client.calls.count('get')
        with self.assertRaisesRegex(ContractError, 'SOURCE_READ_REJECTED'):
            store.read(self.plan, 'auth-postgres')
        self.assertEqual(client.calls.count('get'), gets)

    def test_sdk_identity_and_region_before_mutation(self):
        client = FakeSecretsManager()
        for account, region in (('000000000000', 'us-east-1'), (ACCOUNT, 'sa-east-1')):
            client.meta.region_name = region
            store = SecretsManagerStore(client, {}, lambda: {'Account': account})
            with self.assertRaisesRegex(ContractError, 'IDENTITY_MISMATCH'):
                store.put_once(self.plan, 'auth-postgres', self.bundle.payloads['auth-postgres'])
        self.assertEqual(client.calls, [])

    def test_rds_managed_secret_bound_to_exact_database(self):
        client = FakeSecretsManager()
        arn = self.plan.infra['rds']['master_secret_arn']
        client.records[arn] = {'ARN': arn, 'Name': 'rds-managed', 'Tags': [], 'SecretString': json.dumps(self.master), 'VersionId': 'synthetic-version'}
        identifier = f'{self.plan.scope.namespace}-postgres'
        database = {'DBInstanceIdentifier': identifier, 'DBInstanceArn': f'arn:aws:rds:us-east-1:{ACCOUNT}:db:{identifier}',
                    'MasterUserSecret': {'SecretArn': arn}, 'Endpoint': {'Address': self.plan.infra['rds']['address'], 'Port': 5432},
                    'Engine': 'postgres', 'EngineVersion': '17.10'}
        reader = SecretsManagerReader(client, {'postgres-bootstrap-admin': arn}, lambda: {'Account': ACCOUNT}, lambda requested: database)
        self.assertEqual(reader.read(self.plan, 'postgres-bootstrap-admin')['payload'], self.master)
        database['DBInstanceIdentifier'] = 'tc-oficina-p4-demo-b-postgres'
        gets = client.calls.count('get')
        with self.assertRaises(ContractError):
            reader.read(self.plan, 'postgres-bootstrap-admin')
        self.assertEqual(client.calls.count('get'), gets)

    def test_preparation_reuses_all_credentials_and_certificates(self):
        count = len(self.source.records)
        repeated = prepare(self.plan, self.source, self.master, [self.public_root], self.external)
        for name in repeated.payloads:
            self.assertTrue(equivalent_payload(name, repeated.payloads[name], self.bundle.payloads[name]))
        target = MemoryTarget()
        receipts = deliver(self.plan, target, self.bundle)
        self.assertEqual(deliver(self.plan, target, repeated), receipts)
        self.assertEqual(len(self.source.records), count)
        self.assertNotIn(self.plan.canonical_name('postgres-bootstrap-admin'), self.source.records)
        self.assertNotIn(self.plan.canonical_name('rabbitmq-definitions'), self.source.records)

    def test_missing_prerequisite_does_not_generate_source(self):
        for roots, external in (([], self.external), ([self.public_root], {})):
            source = MemorySource()
            with self.assertRaises(ContractError):
                prepare(self.plan, source, self.master, roots, external)
            self.assertEqual(source.records, {})

    def test_pki_cryptographic_validation(self):
        for service in ('auth-internal', 'os-service', 'execution-service', 'billing-service'):
            payload = self.bundle.payloads[f'{service}-tls']
            validate_identity(payload, 'demo-a', service, self.plan.scope.namespace, self.bundle.ca_certificate, [self.public_root])
            trust = pkcs12.load_pkcs12(base64.b64decode(payload['truststore.p12']), payload['password'].encode())
            self.assertEqual(len(trust.additional_certs), 1 if service == 'auth-internal' else 2)

    def test_wrong_san_subject_expiry_and_environment(self):
        payload = self.bundle.payloads['os-service-tls']
        arguments = (payload, 'demo-a', 'os-service', self.plan.scope.namespace, self.bundle.ca_certificate, [self.public_root])
        for changed in (
                (payload, 'demo-a', 'os-service', 'wrong-namespace', self.bundle.ca_certificate, [self.public_root]),
                (payload, 'demo-a', 'billing-service', self.plan.scope.namespace, self.bundle.ca_certificate, [self.public_root]),
                (payload, 'demo-b', 'os-service', self.plan.scope.namespace, self.bundle.ca_certificate, [self.public_root])):
            with self.assertRaises(InvalidMaterial):
                validate_identity(*changed)
        with self.assertRaises(InvalidMaterial):
            validate_identity(*arguments, now=datetime.now(timezone.utc) + timedelta(days=4))

    def test_tampered_store_and_wrong_password(self):
        for changes in ({'password': 'B' * 48}, {'identity.p12': base64.b64encode(b'invalid-store').decode()}):
            payload = dict(self.bundle.payloads['auth-internal-tls'], **changes)
            with self.assertRaises(InvalidMaterial):
                validate_identity(payload, 'demo-a', 'auth-internal', self.plan.scope.namespace, self.bundle.ca_certificate, [self.public_root])

    def test_signed_certificate_missing_san_wrong_subject_or_eku(self):
        payload = self.bundle.payloads['os-service-tls']
        private_key, certificate, chain = pkcs12.load_key_and_certificates(base64.b64decode(payload['identity.p12']), payload['password'].encode())
        ca = self.source.read(self.plan, 'technical-ca')['payload']
        ca_key, _, _ = pkcs12.load_key_and_certificates(base64.b64decode(ca['ca.p12']), ca['password'].encode())
        for failure_case in ('missing-san', 'wrong-subject', 'wrong-eku'):
            subject = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, 'billing-service')]) if failure_case == 'wrong-subject' else certificate.subject
            builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(certificate.issuer)
                       .public_key(private_key.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(certificate.not_valid_before_utc).not_valid_after(certificate.not_valid_after_utc)
                       .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True))
            if failure_case != 'missing-san':
                builder = builder.add_extension(certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value, critical=False)
            usage = x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]) if failure_case == 'wrong-eku' else certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            forged = builder.add_extension(usage, critical=False).sign(ca_key, hashes.SHA256())
            identity = pkcs12.serialize_key_and_certificates(b'os-service', private_key, forged, chain, serialization.BestAvailableEncryption(payload['password'].encode()))
            with self.subTest(case=failure_case), self.assertRaises(InvalidMaterial):
                validate_identity(dict(payload, **{'identity.p12': base64.b64encode(identity).decode()}), 'demo-a', 'os-service', self.plan.scope.namespace, self.bundle.ca_certificate, [self.public_root])

    def test_delivery_idempotence_and_refuse_rotation(self):
        target = MemoryTarget()
        receipts = deliver(self.plan, target, self.bundle)
        self.assertEqual(deliver(self.plan, target, self.bundle), receipts)
        changed = copy.deepcopy(self.bundle)
        changed.payloads['auth-postgres']['password'] = 'B' * 48
        with self.assertRaisesRegex(ContractError, 'ROTATION_REQUIRED'):
            deliver(self.plan, target, changed)

    def test_delivery_wrong_owner(self):
        target = MemoryTarget()
        deliver(self.plan, target, self.bundle)
        target.records[(self.plan.scope.namespace, 'auth-postgres')]['metadata']['labels']['oficina/owner'] = 'unexpected'
        with self.assertRaisesRegex(ContractError, 'TARGET_OWNERSHIP_MISMATCH'):
            deliver(self.plan, target, self.bundle)

    def test_cleanup_temporary_only_other_environment_untouched(self):
        target = MemoryTarget()
        receipts = deliver(self.plan, target, self.bundle)
        other_key = (fixture('demo-b').scope.namespace, 'postgres-bootstrap-admin')
        target.records[other_key] = {'preserve': True}
        with self.assertRaises(ContractError):
            target.remove_temporary(self.plan, 'bootstrap', receipts, False)
        with self.assertRaises(ContractError):
            target.remove_temporary(self.plan, 'environment', receipts, True)
        target.remove_temporary(self.plan, 'bootstrap', receipts, True)
        target.remove_temporary(self.plan, 'migration', receipts, True)
        target.remove_temporary(self.plan, 'migration', receipts, True)
        self.assertEqual(len(target.records), 17)
        self.assertEqual(target.records[other_key], {'preserve': True})

    def test_cleanup_uid_changed_rejected(self):
        target = MemoryTarget()
        receipts = deliver(self.plan, target, self.bundle)
        target.records[(self.plan.scope.namespace, 'auth-migration-postgres')]['metadata']['uid'] = 'replacement'
        with self.assertRaises(ContractError):
            target.remove_temporary(self.plan, 'migration', receipts, True)

    def test_kubernetes_transport_encoded_once_and_conditional_delete(self):
        api = FakeKubernetes()
        namespace = lambda name: {'metadata': {'name': name, 'labels': {'oficina/environment': 'demo-a', 'oficina/managed-by': 'platform'}}}
        target = KubernetesTarget(api, namespace, lambda: self.plan.infra['eks'])
        receipts = deliver(self.plan, target, self.bundle)
        self.assertEqual(deliver(self.plan, target, self.bundle), receipts)
        data = api.records[(self.plan.scope.namespace, 'auth-internal-tls')]['data']
        self.assertEqual(data['identity.p12'], self.bundle.payloads['auth-internal-tls']['identity.p12'])
        target.remove_temporary(self.plan, 'bootstrap', receipts, True)
        self.assertEqual(api.deletions, [(self.plan.scope.namespace, 'postgres-bootstrap-admin')])

    def test_wrong_cluster_and_namespace_and_sdk_error_sanitized(self):
        api = FakeKubernetes()
        namespace = lambda name: {'metadata': {'name': name, 'labels': {'oficina/environment': 'demo-b', 'oficina/managed-by': 'platform'}}}
        target = KubernetesTarget(api, namespace, lambda: self.plan.infra['eks'])
        with self.assertRaisesRegex(ContractError, 'NAMESPACE_OWNERSHIP_MISMATCH'):
            deliver(self.plan, target, self.bundle)
        target = KubernetesTarget(api, namespace, lambda: dict(self.plan.infra['eks'], arn='wrong-cluster'))
        with self.assertRaisesRegex(ContractError, 'CLUSTER_MISMATCH'):
            deliver(self.plan, target, self.bundle)
        def broken_namespace(name):
            raise RuntimeError('private-sdk-response-do-not-log')
        target = KubernetesTarget(api, broken_namespace, lambda: self.plan.infra['eks'])
        with self.assertRaisesRegex(ContractError, '^TRANSPORT_REJECTED$'):
            deliver(self.plan, target, self.bundle)
        self.assertEqual(api.records, {})

    def test_destroy_requires_full_inventory_exact_confirmation(self):
        names = {name for name, spec in self.plan.specs.items() if spec.source in ('secrets-manager', 'external')} | {'technical-ca'}
        inventory = {name: f'arn:aws:secretsmanager:us-east-1:{ACCOUNT}:secret:{self.plan.canonical_name(name)}-ABC123' for name in names}
        confirmation = f'DESTROY demo-a {ACCOUNT} {self.plan.scope.namespace}'
        self.assertEqual(len(destroy_inventory(self.plan, inventory, confirmation, True)), len(names))
        for confirmation_value in ('DESTROY demo-b', '', confirmation.replace(ACCOUNT, '000000000000')):
            with self.assertRaises(ContractError):
                destroy_inventory(self.plan, inventory, confirmation_value, True)
        inventory['auth-postgres'] = inventory['auth-postgres'].replace('demo-a', 'demo-b')
        with self.assertRaises(ContractError):
            destroy_inventory(self.plan, inventory, confirmation, True)

    def test_release_gate(self):
        image = f'{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/{self.plan.scope.namespace}-auth@sha256:' + 'a' * 64
        job = f'auth-migrate-{REVISION[:12]}-' + 'a' * 12
        evidence = {'environment': 'demo-a', 'namespace': self.plan.scope.namespace,
                    'databases': self.plan.infra['rds']['application_databases'], 'auth_owner': 'oficina_auth_migrator',
                    'grants_verified': True, 'runtime_ddl_denied': True,
                    'migration': {'name': job, 'image': image, 'uid': 'synthetic-job-uid', 'complete': True, 'failed': False}}
        self.assertTrue(release_ready(self.plan, evidence, job, image))
        evidence['migration']['complete'] = False
        with self.assertRaises(ContractError):
            release_ready(self.plan, evidence, job, image)

    def test_no_values_in_logs_or_summary(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            target = MemoryTarget()
            deliver(self.plan, target, self.bundle)
            print(json.dumps(self.plan.summary()))
            try:
                validate_payload(self.plan, 'auth-postgres', {'password': 'do-not-log-this'})
            except ContractError as failure:
                print(str(failure))
        logs = output.getvalue()
        for payload in self.bundle.payloads.values():
            for key, value in payload.items():
                if key not in ('username',) and value:
                    self.assertNotIn(value, logs)
        self.assertNotIn('do-not-log-this', logs)
        for record in self.source.records.values():
            for key, value in record['payload'].items():
                if key != 'username' and value:
                    self.assertNotIn(value, logs)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(ContractError):
            parse_json('{"password":"first","password":"second"}')

    def test_file_guard(self):
        private_marker = ('-----BEGIN ' + 'PRIVATE KEY-----').encode()
        for name, content in [('private.pem', private_marker), ('identity.p12', b'binary'),
                              ('.env', b'example=value'), ('secret.yaml', b'kind: Secret\ndata: {}'),
                              ('bundle.json', b'{"kind":"List","items":[{"kind":"Secret"}]}'),
                              ('token.txt', ('AKIA' + 'Z' * 16).encode())]:
            self.assertTrue(unsafe_file(name, content))
        self.assertFalse(unsafe_file('public.pem', b'public certificate only'))
        self.assertFalse(unsafe_file('metadata.json', b'{"keys":["password"],"source":"secrets-manager"}'))

    def test_enabled_payment_requires_external_credentials(self):
        plan = copy.copy(self.plan)
        object.__setattr__(plan, 'payment_enabled', True)
        with self.assertRaisesRegex(ContractError, 'EXTERNAL_SOURCE_MISSING'):
            validate_payload(plan, 'billing-mercado-pago', self.external['billing-mercado-pago'])

    @unittest.skipUnless(os.environ.get('PLATFORM_PUBLIC_CONTRACT'), 'Requires Terraform public fixture')
    def test_actual_terraform_contract_compatibility(self):
        contract = parse_json(Path(os.environ['PLATFORM_PUBLIC_CONTRACT']).read_text())
        plan = Plan.build(self.plan.scope, contract['infra_contract'], contract['secret_contract'], contract['secret_delivery_contract']['mercado_pago_enabled'])
        for name, spec in plan.specs.items():
            self.assertEqual(spec.lifecycle, contract['secret_delivery_contract']['secrets'][name]['lifecycle'])

    @unittest.skipUnless(shutil.which('java') and hasattr(os, 'memfd_create'), 'Requires Java 21 and Linux memfd')
    def test_actual_java_pkcs12_and_secret_argument_file(self):
        self.verify_java_stores(self.bundle, 2)

    @unittest.skipUnless(shutil.which('keytool') and hasattr(os, 'memfd_create'), 'Requires Java 21 and Linux memfd')
    def test_full_jre_roots_derived_outside_canonical_secret(self):
        result = subprocess.run(['keytool', '-list', '-rfc', '-cacerts', '-storepass:env', 'PUBLIC_JRE_STORE_PASSWORD'],
                                env=dict(os.environ, PUBLIC_JRE_STORE_PASSWORD='changeit'), capture_output=True, check=True)
        now = datetime.now(timezone.utc)
        roots = [x509.load_pem_x509_certificate(block) for block in re.findall(rb'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', result.stdout, re.DOTALL)]
        roots = [root for root in roots if root.not_valid_before_utc <= now < root.not_valid_after_utc]
        self.assertGreater(len(roots), 30)
        repeated = prepare(self.plan, self.source, self.master, roots, self.external)
        self.assertGreater(len(repeated.payloads['os-service-tls']['truststore.p12']), 65536)
        for record in self.source.records.values():
            self.assertLessEqual(len(json.dumps(record['payload']).encode()), 65536)
        deliver(self.plan, MemoryTarget(), repeated)
        self.verify_java_stores(repeated, len(roots) + 1)

    def verify_java_stores(self, bundle, root_count):
        lines = ['4']
        for service in ('os-service', 'execution-service', 'billing-service', 'auth-internal'):
            payload = bundle.payloads[f'{service}-tls']
            lines.extend([service, payload['password'], payload['identity.p12'], payload['truststore.p12'], '1' if service == 'auth-internal' else str(root_count)])
            lines.append(payload.get('inbound-truststore.p12', payload['truststore.p12']))
        descriptor = os.memfd_create('java-options')
        try:
            os.write(descriptor, bundle.payloads['os-service-tls']['java-options'].encode())
            result = subprocess.run(['java', f'@/proc/self/fd/{descriptor}', str(Path(__file__).with_name('CheckStores.java'))],
                                    input=('\n'.join(lines) + '\n').encode(), capture_output=True, pass_fds=(descriptor,))
            self.assertEqual(result.returncode, 0, 'Java PKCS12/argument-file verification failed; payload suppressed')
            self.assertEqual(result.stdout.decode().strip(), 'PASS Java 21 PKCS12 and argument file')
        finally:
            os.close(descriptor)


if __name__ == '__main__':
    unittest.main()
