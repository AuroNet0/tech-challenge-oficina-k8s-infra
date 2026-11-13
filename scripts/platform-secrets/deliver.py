import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import platform_secrets as contracts
import transports


ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENT = 'demo-a'
SERVICES = ('os', 'execution', 'billing', 'auth')


def read_json(filename):
    return contracts.parse_json(Path(filename).read_text(encoding='utf-8-sig'))


def output_value(outputs, name):
    value = outputs[name]
    if isinstance(value, dict) and {'value', 'type', 'sensitive'} <= value.keys():
        return value['value']
    return value


def aws_context(account=None, region=None):
    import boto3

    catalog = read_json(ROOT / 'phase4/environments/catalog.json')
    settings = catalog['environments'][ENVIRONMENT]
    account = account or settings['aws_account_id']
    region = region or settings['aws_region']
    contracts.check(account == settings['aws_account_id'] and region == settings['aws_region'], 'IDENTITY_MISMATCH')
    session = boto3.Session(region_name=region)
    identity = session.client('sts').get_caller_identity()
    contracts.check(identity['Account'] == account, 'IDENTITY_MISMATCH')
    return catalog, session, account, region


def build_plan(catalog, session, account, region, revision, infra, secret_contract):
    scope = contracts.Scope.resolve(catalog, ENVIRONMENT, account, region,
                                    session.client('sts').get_caller_identity()['Account'],
                                    session.region_name, revision)
    scope.require_ready()
    return contracts.Plan.build(scope, infra, secret_contract, payment_enabled=True)


class DurableStore(transports.SecretsManagerStore):
    def contains(self, plan, name):
        from botocore.exceptions import ClientError

        self._context(plan)
        if name not in self.inventory:
            try:
                observed = self.client.describe_secret(SecretId=plan.canonical_name(name))
            except ClientError as error:
                if error.response['Error']['Code'] != 'ResourceNotFoundException':
                    raise
            else:
                transports.validate_arn(plan, name, observed['ARN'])
                contracts.check(observed['Name'] == plan.canonical_name(name) and not observed.get('DeletedDate'),
                                'SECRET_OWNERSHIP_REJECTED')
                actual = {tag['Key']: tag['Value'] for tag in observed.get('Tags', [])}
                contracts.check(all(actual.get(key) == value for key, value in
                                    transports.canonical_tags(plan, name).items()), 'SECRET_OWNERSHIP_REJECTED')
                self.inventory[name] = observed['ARN']
        return super().contains(plan, name)


def kubernetes_target(session, plan):
    from kubernetes import client, config

    config.load_kube_config()
    api_client = client.ApiClient()
    api = transports.KubernetesApi(client.CoreV1Api(api_client), api_client)

    def connection():
        cluster = session.client('eks').describe_cluster(name=plan.scope.namespace)['cluster']
        configuration = api_client.configuration
        certificate = base64.b64decode(cluster['certificateAuthority']['data'], validate=True)
        contracts.check(configuration.verify_ssl and configuration.ssl_ca_cert
                        and configuration.host.rstrip('/') == cluster['endpoint'].rstrip('/')
                        and Path(configuration.ssl_ca_cert).read_bytes().strip() == certificate.strip(),
                        'KUBECONFIG_CLUSTER_MISMATCH')
        return dict(arn=cluster['arn'], endpoint=cluster['endpoint'],
                    certificate_authority_data=cluster['certificateAuthority']['data'])

    return transports.KubernetesTarget(api, api.read_namespace, connection), api_client


def validate_images(plan, images):
    contracts.check(set(images) == set(SERVICES) | {'mongodb', 'rabbitmq', 'postgres'}, 'IMAGE_CONTRACT_REJECTED')
    for service in SERVICES:
        expected = (fr'{plan.scope.account}\.dkr\.ecr\.{re.escape(plan.scope.region)}\.amazonaws\.com/'
                    fr'{re.escape(plan.scope.namespace)}-{service}@sha256:[0-9a-f]{{64}}')
        contracts.check(isinstance(images[service], str) and re.fullmatch(expected, images[service]),
                        'SERVICE_IMAGE_REJECTED')
    for name in ('mongodb', 'rabbitmq', 'postgres'):
        contracts.check(isinstance(images[name], str)
                        and re.fullmatch(r'[a-z0-9][a-z0-9./:_-]+@sha256:[0-9a-f]{64}', images[name]),
                        'IMAGE_CONTRACT_REJECTED')


def docker_run(arguments, password=None):
    result = subprocess.run(arguments, input=password, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            check=False)
    contracts.check(result.returncode == 0, 'DOCKER_OPERATION_FAILED')
    return result.stdout


def public_roots(session, plan, images):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    validate_images(plan, images)
    registry = f'{plan.scope.account}.dkr.ecr.{plan.scope.region}.amazonaws.com'
    authorization = session.client('ecr').get_authorization_token()['authorizationData']
    contracts.check(len(authorization) == 1, 'ECR_AUTH_DESTINATION_REJECTED')
    credentials = authorization[0]
    username, password = base64.b64decode(credentials['authorizationToken'], validate=True).split(b':', 1)
    contracts.check(username == b'AWS' and credentials['proxyEndpoint'] == 'https://' + registry,
                    'ECR_AUTH_DESTINATION_REJECTED')
    reference_roots = None
    with tempfile.TemporaryDirectory(prefix='platform-secrets-docker-') as directory:
        docker = ['docker', '--config', directory]
        docker_run([*docker, 'login', '--username', 'AWS', '--password-stdin', registry], password)
        for service in SERVICES:
            image = images[service]
            docker_run([*docker, 'pull', image])
            output = docker_run([*docker, 'run', '--rm', '--network=none', '--read-only', '--entrypoint',
                                 'keytool', image, '-list', '-rfc', '-cacerts', '-storepass', 'changeit'])
            certificates = x509.load_pem_x509_certificates(output)
            current = {certificate.fingerprint(hashes.SHA256()): certificate for certificate in certificates}
            contracts.check(current, 'PRODUCTION_PUBLIC_ROOTS_MISSING')
            if reference_roots is not None:
                current = {fingerprint: certificate for fingerprint, certificate in current.items()
                           if fingerprint in reference_roots}
                contracts.check(current, 'NO_COMMON_PRODUCTION_PUBLIC_ROOTS')
            reference_roots = current
    return tuple(reference_roots.values())


def external_payment(source, plan, arn):
    contracts.check(re.fullmatch(fr'arn:aws:secretsmanager:{re.escape(plan.scope.region)}:{plan.scope.account}'
                                r':secret:[A-Za-z0-9/_+=.@!-]+-[A-Za-z0-9]{6}', arn), 'EXTERNAL_SOURCE_ARN_REJECTED')
    description = source.client.describe_secret(SecretId=arn)
    excluded = {plan.canonical_name(name) for name in plan.specs} | {
        plan.canonical_name('technical-ca'), f'/oficina/phase4/{ENVIRONMENT}/auth-signing'}
    contracts.check(description['ARN'] == arn and not description.get('DeletedDate')
                    and description['Name'] not in excluded
                    and arn != plan.infra['rds']['master_secret_arn'], 'EXTERNAL_SOURCE_ARN_REJECTED')
    response = source.client.get_secret_value(SecretId=arn, VersionStage='AWSCURRENT')
    contracts.check(response['ARN'] == arn and bool(response['VersionId']), 'EXTERNAL_SOURCE_ARN_REJECTED')
    payload = contracts.parse_json(response['SecretString'])
    contracts.validate_payload(plan, 'billing-mercado-pago', payload)
    contracts.check(payload['access-token'].startswith(('TEST-', 'APP_USR-')) and bool(payload['webhook-secret']),
                    'MERCADO_PAGO_TEST_CREDENTIAL_REQUIRED')
    return payload


def write_receipts(filename, document):
    destination = Path(filename)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.resolve().parent,
                                         prefix='.platform-receipts-', delete=False) as output:
            temporary = Path(output.name)
            json.dump(document, output, indent=2)
            output.write('\n')
        os.replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def deliver(arguments):
    catalog, session, account, region = aws_context(arguments.account, arguments.region)
    infra = read_json(arguments.infra_contract)
    if 'platform_contract' in infra:
        infra = output_value(infra, 'platform_contract')
    elif {'value', 'type', 'sensitive'} <= infra.keys():
        infra = infra['value']
    secret_contract = output_value(read_json(arguments.platform_outputs), 'secret_contract')
    plan = build_plan(catalog, session, account, region, arguments.revision, infra, secret_contract)
    images = read_json(arguments.images)
    validate_images(plan, images)
    source = DurableStore(session.client('secretsmanager'),
                          {'postgres-bootstrap-admin': infra['rds']['master_secret_arn']},
                          session.client('sts').get_caller_identity,
                          lambda name: session.client('rds').describe_db_instances(
                              DBInstanceIdentifier=name)['DBInstances'][0])
    target, api_client = kubernetes_target(session, plan)
    try:
        master = source.read(plan, 'postgres-bootstrap-admin')['payload']
        external = external_payment(source, plan, arguments.mercado_pago_secret_arn)
        roots = public_roots(session, plan, images)
        bundle = contracts.prepare(plan, source, master, roots, {'billing-mercado-pago': external})
        receipts = contracts.deliver(plan, target, bundle)
        write_receipts(arguments.receipts_out, {
            'schema_version': 1, 'environment': ENVIRONMENT, 'account': account, 'region': region,
            'revision': arguments.revision, 'infra_contract': infra, 'secret_contract': secret_contract,
            'images': images, 'uids': receipts})
        for name in receipts:
            print(name)
    finally:
        api_client.close()


def cleanup_temporary(arguments):
    from kubernetes import client

    document = read_json(arguments.receipts)
    contracts.check(document['schema_version'] == 1 and document['environment'] == ENVIRONMENT)
    catalog, session, account, region = aws_context(document['account'], document['region'])
    plan = build_plan(catalog, session, account, region, document['revision'],
                      document['infra_contract'], document['secret_contract'])
    images = document['images']
    validate_images(plan, images)
    target, api_client = kubernetes_target(session, plan)
    try:
        if arguments.lifecycle == 'bootstrap':
            job_name = f'postgres-bootstrap-{plan.scope.revision[:12]}'
            expected_image = images['postgres']
        else:
            job_name = f'auth-migrate-{plan.scope.revision[:12]}-{images["auth"].split("@sha256:")[1][:12]}'
            expected_image = images['auth']
        job = api_client.sanitize_for_serialization(
            client.BatchV1Api(api_client).read_namespaced_job(job_name, plan.scope.namespace))
        metadata = job['metadata']
        labels = metadata.get('labels', {})
        status = job.get('status', {})
        contracts.check(metadata['name'] == job_name and metadata['namespace'] == plan.scope.namespace
                        and labels.get('oficina/environment') == ENVIRONMENT
                        and labels.get('oficina/managed-by') == 'platform'
                        and job['spec']['template']['spec']['containers'][0]['image'] == expected_image,
                        'CLEANUP_JOB_MISMATCH')
        completed = (status.get('succeeded', 0) > 0 and not status.get('failed', 0)
                     and any(condition['type'] == 'Complete' and condition['status'] == 'True'
                             for condition in status.get('conditions', [])))
        target.remove_temporary(plan, arguments.lifecycle, document['uids'], completed)
        for name, spec in plan.specs.items():
            if spec.lifecycle == arguments.lifecycle:
                print(name)
    finally:
        api_client.close()


def destroy_secrets(arguments):
    from botocore.exceptions import ClientError

    _, session, account, region = aws_context(arguments.account, arguments.region)
    source = session.client('secretsmanager')
    names = {name for name, spec in contracts.specifications().items()
             if spec.source in ('secrets-manager', 'external')} | {'technical-ca'}
    pending = []
    for name in sorted(names):
        canonical = f'/oficina/phase4/{ENVIRONMENT}/{name}'
        try:
            observed = source.describe_secret(SecretId=canonical)
        except ClientError as error:
            if error.response['Error']['Code'] == 'ResourceNotFoundException':
                continue
            raise
        arn = observed['ARN']
        contracts.check(observed['Name'] == canonical and re.fullmatch(
            fr'arn:aws:secretsmanager:{re.escape(region)}:{account}:secret:{re.escape(canonical)}-[A-Za-z0-9]{{6}}',
            arn), 'SECRET_DESTROY_SCOPE_REJECTED')
        tags = {tag['Key']: tag['Value'] for tag in observed.get('Tags', [])}
        contracts.check(tags.get('EnvironmentId') == ENVIRONMENT and tags.get('ManagedBy') == 'platform-secrets'
                        and tags.get('Lifecycle') == 'ephemeral', 'SECRET_DESTROY_OWNERSHIP_REJECTED')
        if not observed.get('DeletedDate'):
            pending.append(arn)
    for arn in pending:
        observed = source.describe_secret(SecretId=arn)
        tags = {tag['Key']: tag['Value'] for tag in observed.get('Tags', [])}
        contracts.check(observed['ARN'] == arn and tags.get('EnvironmentId') == ENVIRONMENT
                        and tags.get('ManagedBy') == 'platform-secrets' and tags.get('Lifecycle') == 'ephemeral',
                        'SECRET_DESTROY_OWNERSHIP_REJECTED')
        if not observed.get('DeletedDate'):
            source.delete_secret(SecretId=arn, ForceDeleteWithoutRecovery=True)
            print(arn)


def main():
    parser = argparse.ArgumentParser(description='Deliver demo-a platform secrets without a driver or journal.')
    commands = parser.add_subparsers(dest='command', required=True)
    delivery = commands.add_parser('deliver', help='Prepare canonical secrets and project them into the current kubeconfig cluster.')
    for option in ('infra-contract', 'platform-outputs', 'revision', 'images', 'mercado-pago-secret-arn',
                   'region', 'account', 'receipts-out'):
        delivery.add_argument('--' + option, required=True)
    delivery.set_defaults(operation=deliver)
    cleanup = commands.add_parser('cleanup-temporary', help='Remove temporary projections after verifying their completed Kubernetes Job.')
    cleanup.add_argument('--lifecycle', choices=('bootstrap', 'migration'), required=True)
    cleanup.add_argument('--receipts', required=True, help='Receipts and public context written by deliver.')
    cleanup.set_defaults(operation=cleanup_temporary)
    destroy = commands.add_parser('destroy-secrets', help='Force-delete only canonical platform-secrets ephemeral secrets; preserve input secrets and auth-signing.')
    destroy.add_argument('--region', help='Defaults to demo-a region in catalog.json.')
    destroy.add_argument('--account', help='Defaults to demo-a account in catalog.json; verified through STS.')
    destroy.set_defaults(operation=destroy_secrets)
    arguments = parser.parse_args()
    try:
        arguments.operation(arguments)
    except Exception as error:
        reason = 'UNEXPECTED_ERROR'
        if (isinstance(error, contracts.ContractError) and error.args and isinstance(error.args[0], str)
                and re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', error.args[0])):
            reason = error.args[0]
        step = arguments.command
        error_traceback = error.__traceback__
        while error_traceback is not None:
            if error_traceback.tb_frame.f_code.co_filename == __file__:
                step = error_traceback.tb_frame.f_code.co_name
            error_traceback = error_traceback.tb_next
        print(f'PLATFORM_SECRETS_OPERATION_REJECTED step={step} reason={reason}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
