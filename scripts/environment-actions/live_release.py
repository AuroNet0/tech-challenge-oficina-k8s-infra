import base64
import json
import os
from pathlib import Path
import re
import urllib.request
from contextlib import contextmanager

from runtime import ACCOUNT, PREFIX, REGION, ROOT, Failure, canonical, cleanup_steps, digest, module_at, require


@contextmanager
def registry_login(driver):
    authorization = driver.api('ecr', 'get_authorization_token')['authorizationData'][0]
    user, password = base64.b64decode(authorization['authorizationToken']).split(b':', 1)
    registry = f'{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com'
    require(user == b'AWS' and authorization['proxyEndpoint'] == 'https://' + registry, 'ECR_AUTH_DESTINATION_REJECTED')
    directory = Path(driver.temporary.name) / 'ecr-login'
    directory.mkdir(exist_ok=True)
    docker = ['docker', '--config', str(directory)]
    try:
        driver.run([*docker, 'login', '--username', 'AWS', '--password-stdin', registry], password)
        yield docker
    finally:
        if (directory / 'config.json').exists():
            (directory / 'config.json').unlink()


def recovered_jar(driver, reference):
    destination = Path(driver.temporary.name) / 'recovered-auth.jar'
    with registry_login(driver) as docker:
        driver.run([*docker, 'pull', reference])
        container = driver.run([*docker, 'create', reference]).strip()
        require(re.fullmatch(r'[0-9a-f]{64}', container), 'CONTAINER_ID_REJECTED')
        original_error = None
        try:
            driver.run([*docker, 'cp', container + ':/app/app.jar', str(destination)])
        except BaseException as error:
            original_error = error
            raise
        finally:
            cleanup_steps(driver.report, [('build-container', lambda: driver.run([*docker, 'rm', container]))], original_error)
    module_at('release_jar_contract', 'scripts/artifact-release', 'release.py').verify_jar(destination)
    return destination


def ca_material(driver):
    url = 'https://truststore.pki.rds.amazonaws.com/us-east-1/us-east-1-bundle.pem'
    with urllib.request.urlopen(url, timeout=30) as response:
        material = response.read(1024 * 1024)
    policy = json.loads((ROOT / 'scripts/artifact-release/rds-ca-policy.json').read_text())
    entry = policy.get(digest(material), {})
    require(entry.get('source') == url and entry.get('region') == REGION, 'APPROVED_RDS_CA_REQUIRED')
    require(b'PRIVATE KEY' not in material and material.startswith(b'-----BEGIN CERTIFICATE-----'), 'CA_FORMAT_REJECTED')
    return material


def readback_image(driver, service, revision, image_id):
    repository = PREFIX + '-' + service
    result = driver.api('ecr', 'batch_get_image', repositoryName=repository, imageIds=[dict(imageTag=revision)])
    require(not result.get('failures') and len(result.get('images', [])) == 1, 'ECR_READBACK_FAILED', repository)
    image = result['images'][0]
    manifest = image['imageManifest'].encode()
    require(image['imageId']['imageDigest'] == 'sha256:' + digest(manifest), 'ECR_MANIFEST_MISMATCH')
    require(json.loads(manifest).get('config', {}).get('digest') == image_id, 'IMMUTABLE_IMAGE_CONTENT_MISMATCH')
    return f'{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{repository}@{image["imageId"]["imageDigest"]}'


def release(driver):
    builder = module_at('release_contract', 'scripts/artifact-release', 'release.py')
    ca = ca_material(driver)
    ca_file = Path(driver.temporary.name) / 'rds-ca.pem'
    ca_file.write_bytes(ca)
    infra = driver.outputs('infra')['platform_contract']
    bucket = PREFIX + '-artifacts'
    owner = dict(Bucket=bucket, ExpectedBucketOwner=ACCOUNT)
    require(driver.api('s3', 'get_bucket_location', **owner).get('LocationConstraint') in (None, REGION), 'ARTIFACT_BUCKET_REGION_REJECTED')
    require(driver.api('s3', 'get_bucket_versioning', **owner).get('Status') == 'Enabled', 'ARTIFACT_BUCKET_VERSIONING_REQUIRED')
    public = driver.api('s3', 'get_public_access_block', **owner)['PublicAccessBlockConfiguration']
    require(all(public.get(key) for key in ['BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets']), 'ARTIFACT_BUCKET_NOT_PRIVATE')
    jars = {}
    for service in ('auth', 'os', 'execution', 'billing'):
        with driver.report.phase('build-publish-readback', service):
            source = driver.config['services'][service]
            require(source['repository'] == builder.REPOSITORIES[service] and re.fullmatch(r'[0-9a-f]{40}', source['revision']), 'SOURCE_REVISION_REJECTED')
            repository = infra['ecr'][service]
            remote = driver.api('ecr', 'describe_repositories', repositoryNames=[repository['name']])['repositories'][0]
            require(remote['repositoryArn'] == repository['arn'] and remote['imageTagMutability'] == 'IMMUTABLE', 'ECR_REPOSITORY_REJECTED')
            existing = driver.journal['images'].get(service)
            if existing and existing['revision'] == source['revision']:
                try:
                    reference = readback_image(driver, service, source['revision'], existing['image_id'])
                except Failure as error:
                    if error.code != 'ECR_READBACK_FAILED':
                        raise
                else:
                    existing['reference'] = reference
                    driver.save()
                    if service == 'auth' and not driver.journal.get('auth_release'):
                        jars['auth'] = recovered_jar(driver, reference)
                    continue
            checkout = ROOT / 'sources' / service
            output = Path(driver.temporary.name) / ('build-' + service)
            receipt = builder.build_app(service, checkout, source['revision'], output, network_enabled=True)
            image_id = receipt['image']['image_id']
            driver.journal['images'][service] = dict(revision=source['revision'], image_id=image_id, reference=None)
            driver.save()
            authorization = driver.api('ecr', 'get_authorization_token')['authorizationData'][0]
            user, password = base64.b64decode(authorization['authorizationToken']).split(b':', 1)
            registry = f'{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com'
            require(authorization['proxyEndpoint'] == 'https://' + registry and user == b'AWS', 'ECR_AUTH_DESTINATION_REJECTED')
            docker_config = Path(driver.temporary.name) / 'publish-docker'
            docker_config.mkdir(exist_ok=True)
            docker = ['docker', '--config', str(docker_config)]
            try:
                driver.run([*docker, 'login', '--username', 'AWS', '--password-stdin', registry], password)
                local = f'oficina-release-{service}:{source["revision"]}'
                target = repository['url'] + ':' + source['revision']
                driver.run([*docker, 'tag', local, target])
                try:
                    driver.run([*docker, 'push', target])
                except Failure:
                    readback_image(driver, service, source['revision'], image_id)
                driver.journal['images'][service]['reference'] = readback_image(driver, service, source['revision'], image_id)
                driver.save()
            finally:
                if (docker_config / 'config.json').exists():
                    (docker_config / 'config.json').unlink()
            if service == 'auth':
                jars['auth'] = output / 'application.jar'
    with driver.report.phase('auth-jar-publication', bucket):
        revision = driver.config['services']['auth']['revision']
        key = 'auth/' + revision + '/auth.jar'
        previous = driver.journal.get('auth_release')
        if previous and previous['revision'] == revision:
            response = driver.api('s3', 'get_object', **owner, Key=key, VersionId=previous['version_id'])
            require(base64.b64encode(bytes.fromhex(digest(response['Body'].read()))).decode() == previous['sha256_base64'], 'AUTH_JAR_READBACK_FAILED')
        else:
            require('auth' in jars, 'AUTH_JAR_RECOVERY_BUILD_REQUIRED')
            content = jars['auth'].read_bytes()
            pending = dict(key=key, sha256=digest(content), revision=revision)
            driver.journal['pending_jar'] = pending
            if pending not in driver.journal.setdefault('pending_jars', []):
                driver.journal['pending_jars'].append(pending)
            driver.save()
            uploaded = driver.api('s3', 'put_object', **owner, Key=key, Body=content, ServerSideEncryption='AES256', ChecksumAlgorithm='SHA256')
            require(uploaded.get('VersionId') not in (None, 'null'), 'S3_VERSION_ID_REQUIRED')
            driver.journal['objects'].append(dict(bucket=bucket, key=key, version_id=uploaded['VersionId'], sha256=digest(content)))
            driver.save()
            response = driver.api('s3', 'get_object', **owner, Key=key, VersionId=uploaded['VersionId'])
            require(digest(response['Body'].read()) == digest(content), 'AUTH_JAR_READBACK_FAILED')
            driver.journal['auth_release'] = dict(bucket=bucket, key=key, version_id=uploaded['VersionId'],
                sha256_base64=base64.b64encode(bytes.fromhex(digest(content))).decode(), revision=revision)
            driver.save()
    with driver.report.phase('layer-publication-readback', PREFIX):
        layer_output = Path(driver.temporary.name) / 'layer'
        builder.build_layer(os.environ['GITHUB_SHA'], ca_file, layer_output,
            json.loads((ROOT / 'scripts/artifact-release/rds-ca-policy.json').read_text()), network_enabled=True)
        content = (layer_output / 'layer.zip').read_bytes()
        checksum = base64.b64encode(bytes.fromhex(digest(content))).decode()
        driver.journal['pending_layer'] = dict(checksum=checksum, revision=os.environ['GITHUB_SHA'])
        if driver.journal['pending_layer'] not in driver.journal.setdefault('pending_layers', []):
            driver.journal['pending_layers'].append(driver.journal['pending_layer'])
        driver.save()
        versions = driver.items('lambda', 'list_layer_versions', 'LayerVersions', LayerName=PREFIX + '-auth-bootstrap')
        version = None
        for candidate in versions:
            observed = driver.api('lambda', 'get_layer_version', LayerName=PREFIX + '-auth-bootstrap', VersionNumber=candidate['Version'])
            if observed['Content']['CodeSha256'] == checksum:
                version = observed
                break
        if version is None:
            version = driver.api('lambda', 'publish_layer_version', LayerName=PREFIX + '-auth-bootstrap',
                Description='phase4-demo-a:' + os.environ['GITHUB_SHA'], Content=dict(ZipFile=content),
                CompatibleRuntimes=['java21'], CompatibleArchitectures=['x86_64'])
        driver.journal['layers'] = sorted(set(driver.journal['layers'] + [version['LayerVersionArn']]))
        driver.save()
        observed = driver.api('lambda', 'get_layer_version', LayerName=PREFIX + '-auth-bootstrap', VersionNumber=version['Version'])
        require(observed['Content']['CodeSha256'] == checksum, 'LAYER_READBACK_FAILED')
        driver.journal['auth_release'].update(bootstrap_layer_arn=version['LayerVersionArn'], bootstrap_revision=os.environ['GITHUB_SHA'])
        driver.save()
    driver.receipt('release', dict(verified=True, ca_sha256=digest(ca)))


def cleanup_release(driver):
    for service in ('auth', 'os', 'execution', 'billing'):
        repository = PREFIX + '-' + service
        try:
            remote = driver.api('ecr', 'describe_repositories', repositoryNames=[repository])['repositories'][0]
        except Failure as error:
            if error.code == 'RepositoryNotFoundException':
                continue
            raise
        tags = driver.api('ecr', 'list_tags_for_resource', resourceArn=remote['repositoryArn'])['tags']
        require({tag['Key']: tag['Value'] for tag in tags}.get('EnvironmentId') == 'demo-a', 'ECR_OWNERSHIP_REJECTED')
        state_ids = [resource['id'] for resource in driver.journal['resources'].get('infra', []) if resource['type'] == 'aws_ecr_repository']
        require(repository in state_ids, 'ECR_NOT_IN_STATE_INVENTORY')
        images = driver.items('ecr', 'list_images', 'imageIds', repositoryName=repository)
        for offset in range(0, len(images), 100):
            result = driver.api('ecr', 'batch_delete_image', repositoryName=repository, imageIds=images[offset:offset + 100])
            require(not result.get('failures'), 'ECR_DELETE_FAILED')
    pending_jars = driver.journal.get('pending_jars', []) or ([driver.journal['pending_jar']] if driver.journal.get('pending_jar') else [])
    for pending in pending_jars:
        try:
            versions = driver.items('s3', 'list_object_versions', 'Versions', Bucket=PREFIX + '-artifacts', Prefix=pending['key'], ExpectedBucketOwner=ACCOUNT)
        except Failure as error:
            if error.code != 'NoSuchBucket':
                raise
            versions = []
        for version in versions:
            if version['Key'] == pending['key']:
                response = driver.api('s3', 'get_object', Bucket=PREFIX + '-artifacts', Key=version['Key'], VersionId=version['VersionId'], ExpectedBucketOwner=ACCOUNT)
                if digest(response['Body'].read()) == pending['sha256']:
                    entry = dict(bucket=PREFIX + '-artifacts', key=version['Key'], version_id=version['VersionId'], sha256=pending['sha256'])
                    if entry not in driver.journal['objects']:
                        driver.journal['objects'].append(entry)
        driver.save()
    for entry in driver.journal['objects']:
        require(entry['bucket'] == PREFIX + '-artifacts' and re.fullmatch(r'auth/[0-9a-f]{40}/auth.jar', entry['key']), 'S3_ARTIFACT_SCOPE_REJECTED')
        try:
            driver.api('s3', 'delete_object', Bucket=entry['bucket'], Key=entry['key'], VersionId=entry['version_id'], ExpectedBucketOwner=ACCOUNT)
        except Failure as error:
            if error.code not in ('NoSuchBucket', 'NoSuchVersion'):
                raise
    pending_layers = driver.journal.get('pending_layers', []) or ([driver.journal['pending_layer']] if driver.journal.get('pending_layer') else [])
    for pending_layer in pending_layers:
        try:
            versions = driver.items('lambda', 'list_layer_versions', 'LayerVersions', LayerName=PREFIX + '-auth-bootstrap')
        except Failure as error:
            if error.code != 'ResourceNotFoundException':
                raise
            versions = []
        for version in versions:
            observed = driver.api('lambda', 'get_layer_version', LayerName=PREFIX + '-auth-bootstrap', VersionNumber=version['Version'])
            if observed['Content']['CodeSha256'] == pending_layer['checksum']:
                driver.journal['layers'] = sorted(set(driver.journal['layers'] + [observed['LayerVersionArn']]))
        driver.save()
    for arn in driver.journal['layers']:
        require(re.fullmatch(fr'arn:aws:lambda:{REGION}:{ACCOUNT}:layer:{PREFIX}-auth-bootstrap:[1-9][0-9]*', arn), 'LAYER_SCOPE_REJECTED')
        try:
            driver.api('lambda', 'delete_layer_version', LayerName=PREFIX + '-auth-bootstrap', VersionNumber=int(arn.split(':')[-1]))
        except Failure as error:
            if error.code != 'ResourceNotFoundException':
                raise
