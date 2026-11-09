import json
import copy
import os
from pathlib import Path
import re
import urllib.request

from runtime import PREFIX, REGION, Failure, canonical, require


def network_probe(driver):
    image = driver.config.get('network_probe_image', '')
    require(re.fullmatch(r'[a-z0-9./:_-]+@sha256:[0-9a-f]{64}', image), 'PINNED_NETWORK_PROBE_IMAGE_REQUIRED')
    name = 'network-canary-' + os.environ['GITHUB_RUN_ID'][-10:]
    labels = {'oficina/environment': 'demo-a', 'oficina/managed-by': 'actions', 'probe-id': name}
    security = dict(runAsNonRoot=True, runAsUser=10001, seccompProfile=dict(type='RuntimeDefault'))
    container_security = dict(allowPrivilegeEscalation=False, readOnlyRootFilesystem=True, capabilities=dict(drop=['ALL']))
    metadata = dict(namespace=PREFIX, labels=labels)
    server = dict(apiVersion='v1', kind='Pod', metadata=dict(metadata, name=name),
        spec=dict(restartPolicy='Never', automountServiceAccountToken=False, securityContext=security, containers=[
            dict(name='server', image=image, command=['python3', '-m', 'http.server', '18080'], securityContext=container_security,
                readinessProbe=dict(tcpSocket=dict(port=18080), initialDelaySeconds=1, periodSeconds=2))]))
    policy = dict(apiVersion='networking.k8s.io/v1', kind='NetworkPolicy', metadata=dict(metadata, name=name),
        spec=dict(podSelector=dict(matchLabels={'probe-id': name}), policyTypes=['Ingress', 'Egress'],
            ingress=[{'from': [dict(podSelector=dict(matchLabels={'probe-client': name + '-allowed'}))], 'ports': [dict(protocol='TCP', port=18080)]}],
            egress=[{'to': [dict(podSelector=dict(matchLabels={'probe-id': name}))], 'ports': [dict(protocol='TCP', port=18080)]}]))
    driver.apply_bundle(dict(kind='List', items=[server, policy]))
    jobs = []
    try:
        driver.kubectl(['wait', 'pod/' + name, '-n', PREFIX, '--for=condition=Ready', '--timeout=120s'])
        address = json.loads(driver.kubectl(['get', 'pod', name, '-n', PREFIX, '-o', 'json']))['status']['podIP']
        import ipaddress
        require(ipaddress.ip_address(address).is_private, 'PROBE_ADDRESS_REJECTED')
        for allowed in (True, False):
            client_name = name + ('-allowed' if allowed else '-denied')
            code = ('import socket,sys\ntry:\n socket.create_connection((' + repr(address) + ',18080),5).close()\n result=True\n'
                    'except OSError:\n result=False\nsys.exit(0 if result is ' + repr(allowed) + ' else 1)')
            job = dict(apiVersion='batch/v1', kind='Job', metadata=dict(metadata, name=client_name),
                spec=dict(backoffLimit=0, activeDeadlineSeconds=30, template=dict(
                    metadata=dict(labels=dict(labels, **{'probe-client': client_name})),
                    spec=dict(restartPolicy='Never', automountServiceAccountToken=False, securityContext=security,
                        containers=[dict(name='probe', image=image, command=['python3', '-c', code], securityContext=container_security)]))))
            jobs.append(client_name)
            driver.apply_bundle(dict(kind='List', items=[job]))
            driver.kubectl(['wait', 'job/' + client_name, '-n', PREFIX, '--for=condition=complete', '--timeout=60s'])
        driver.receipt('network-policy', dict(verified=True, positive_and_negative_probe=True))
    finally:
        for job in jobs:
            driver.kubectl(['delete', 'job', job, '-n', PREFIX, '--ignore-not-found=true'])
        driver.kubectl(['delete', 'pod', name, '-n', PREFIX, '--ignore-not-found=true'])
        driver.kubectl(['delete', 'networkpolicy', name, '-n', PREFIX, '--ignore-not-found=true'])


def foundation(driver, platform):
    driver.kubectl(['wait', 'nodes', '--all', '--for=condition=Ready', '--timeout=300s'])
    nodes = json.loads(driver.kubectl(['get', 'nodes', '-o', 'json']))['items']
    require(len(nodes) == 2 and all(node['metadata']['labels'].get('node.kubernetes.io/instance-type') == 'm7i-flex.large' for node in nodes), 'TWO_EXPECTED_NODES_REQUIRED')
    driver.apply_bundle(platform['manifests']['foundation'])
    namespace = dict(apiVersion='v1', kind='Namespace', metadata=dict(name=PREFIX + '-observability',
        labels={'oficina/environment': 'demo-a', 'oficina/managed-by': 'platform'}))
    driver.apply_bundle(dict(kind='List', items=[namespace]))
    driver.apply_bundle(platform['executor_rbac'])
    driver.apply_bundle(platform['manifests']['network'])
    for addon in ('vpc-cni', 'aws-ebs-csi-driver', 'eks-pod-identity-agent'):
        observed = driver.api('eks', 'describe_addon', clusterName=PREFIX, addonName=addon)['addon']
        require(observed['status'] == 'ACTIVE' and not observed.get('health', {}).get('issues'), 'ADDON_NOT_HEALTHY', addon)
    driver.kubectl(['rollout', 'status', 'daemonset/aws-node', '-n', 'kube-system', '--timeout=300s'])
    network_probe(driver)


def controller(driver, platform):
    contract = platform['network_controller_contract']
    require(contract['identity']['enabled'] and contract['identity']['role_arn'], 'CONTROLLER_IDENTITY_REQUIRED')
    values_file = Path(driver.temporary.name) / 'alb-values.yaml'
    values_file.write_text(contract['values_yaml'])
    driver.run(['helm', 'upgrade', '--install', PREFIX + '-alb', contract['chart'], '--repo', 'https://aws.github.io/eks-charts',
        '--version', contract['chart_version'], '-n', 'kube-system', '-f', str(values_file), '--wait', '--timeout=300s'])
    driver.kubectl(['rollout', 'status', 'deployment/' + PREFIX + '-alb-aws-load-balancer-controller', '-n', 'kube-system', '--timeout=300s'])
    driver.receipt('controller', dict(verified=True, version=contract['controller_version']))


def health(driver, platform):
    for bundle in ('applications', 'auth'):
        document = json.loads(platform['manifests'][bundle])
        deployments = [item for item in document['items'] if item['kind'] == 'Deployment']
        require(deployments, 'APPLICATION_MANIFESTS_MISSING')
        for item in deployments:
            name = item['metadata']['name']
            driver.kubectl(['rollout', 'status', 'deployment/' + name, '-n', PREFIX, '--timeout=300s'])
            observed = json.loads(driver.kubectl(['get', 'deployment', name, '-n', PREFIX, '-o', 'json']))
            require(observed['spec']['replicas'] == 1 and observed['status'].get('availableReplicas') == 1
                and observed['status'].get('updatedReplicas') == 1
                and observed['status'].get('observedGeneration', 0) >= observed['metadata']['generation'], 'READINESS_NOT_PROVEN', name)
    driver.receipt('health', dict(verified=True, http_readiness_probes_passed=True))


def jwks(driver):
    endpoint = driver.outputs('edge')['jwt_contract']['jwks_uri']
    require(re.fullmatch(r'https://[a-z0-9]+\.execute-api\.us-east-1\.amazonaws.com/\.well-known/jwks.json', endpoint), 'JWKS_URL_REJECTED')
    with urllib.request.urlopen(endpoint, timeout=20) as response:
        document = json.load(response)
        require(response.status == 200 and document.get('keys') and all(key.get('kty') == 'RSA'
            and key.get('kid') and key.get('n') and key.get('e') and not any(field in key for field in ('d', 'p', 'q')) for key in document['keys']), 'JWKS_NOT_READY')
    driver.receipt('jwks', dict(verified=True, key_count=len(document['keys'])))


def alb(driver):
    load_balancers = driver.owned_load_balancers()
    require(len(load_balancers) == 1, 'ALB_NOT_READY')
    load_balancer = load_balancers[0]
    require(load_balancer['State']['Code'] == 'active' and load_balancer['Scheme'] == 'internal', 'ALB_NOT_INTERNAL_ACTIVE')
    listeners = driver.api('elbv2', 'describe_listeners', LoadBalancerArn=load_balancer['LoadBalancerArn'])['Listeners']
    certificate = driver.config['terraform_inputs']['edge']['alb_tls']['certificate_arn']
    listener = next((item for item in listeners if item['Protocol'] == 'HTTPS' and item['Port'] == 443
        and any(cert['CertificateArn'] == certificate for cert in item.get('Certificates', []))), None)
    require(listener, 'EXPECTED_HTTPS_LISTENER_MISSING')
    target_groups = driver.items('elbv2', 'describe_target_groups', 'TargetGroups', LoadBalancerArn=load_balancer['LoadBalancerArn'])
    require(len(target_groups) >= 3, 'BUSINESS_TARGET_GROUPS_MISSING')
    for group in target_groups:
        targets = driver.api('elbv2', 'describe_target_health', TargetGroupArn=group['TargetGroupArn'])['TargetHealthDescriptions']
        require(targets and all(item['TargetHealth']['State'] == 'healthy' for item in targets), 'ALB_TARGETS_UNHEALTHY', group['TargetGroupArn'])
    arns = [load_balancer['LoadBalancerArn'], listener['ListenerArn'], *[group['TargetGroupArn'] for group in target_groups]]
    driver.journal['external_arns'] = sorted(set(driver.journal.get('external_arns', []) + arns))
    driver.receipt('alb', dict(verified=True, listener_arn=listener['ListenerArn']))


def observability(driver, platform):
    contract = platform['observability']
    require(driver.config.get('observability_privileged_approved') is True, 'OBSERVABILITY_NAMESPACE_APPROVAL_REQUIRED')
    value = driver.api('secretsmanager', 'get_secret_value', SecretId=driver.config['new_relic_secret_arn'])['SecretString']
    licence = json.loads(value)['licenseKey']
    from kubernetes import client, config
    config.load_kube_config()
    core = client.CoreV1Api()
    try:
        existing = core.read_namespaced_secret(contract['secret_name'], contract['namespace'])
    except client.ApiException as error:
        if error.status != 404:
            raise Failure('NEW_RELIC_SECRET_ACCESS_FAILED') from None
        core.create_namespaced_secret(contract['namespace'], dict(apiVersion='v1', kind='Secret',
            metadata=dict(name=contract['secret_name'], labels={'oficina/environment': 'demo-a'}), stringData=dict(licenseKey=licence)))
    else:
        require(existing.metadata.labels.get('oficina/environment') == 'demo-a', 'NEW_RELIC_SECRET_OWNERSHIP_REJECTED')
    values = Path(driver.temporary.name) / 'observability.yaml'
    values.write_text(contract['values_yaml'])
    driver.run(['helm', 'upgrade', '--install', PREFIX + '-observability', contract['chart'], '--repo', contract['repository'],
        '--version', contract['version'], '-n', contract['namespace'], '-f', str(values), '--wait', '--timeout=300s'])
    driver.receipt('observability', dict(verified=True, note='Chart ready; telemetry arrival must be checked in New Relic.'))


def verify_databases(driver, platform):
    from live_secrets import secret_context, cleanup_temporary
    contracts, plan, source, target = secret_context(driver)
    for name in ('postgres-bootstrap-admin', 'auth-migration-postgres'):
        payload = source.read(plan, name)['payload']
        uid = target.deliver(plan, name, payload)
        target.verify(plan, name, payload)
        driver.journal['receipts']['secrets']['uids'][name] = uid
        driver.save()
    job = copy.deepcopy(json.loads(platform['manifests']['bootstrap'])['items'][0])
    job['metadata']['name'] = 'postgres-verify-' + os.environ['GITHUB_SHA'][:12]
    job['spec']['template']['spec']['containers'][0]['command'] = ['sh', '-ec',
        'psql -Xq --set=ON_ERROR_STOP=1 --file=/scripts/verify-postgres.sql >/dev/null']
    manifest = dict(kind='List', items=[job])
    driver.apply_bundle(manifest)
    driver.verify_job(json.dumps(manifest))
    driver.receipt('databases', dict(verified=True, grants_and_migrations_verified=True))
    cleanup_temporary(driver, 'bootstrap')
    cleanup_temporary(driver, 'migration')


def up_phase(driver, phase):
    if phase == 'release':
        from live_release import release
        release(driver)
        return
    if phase == 'jwks':
        jwks(driver)
        return
    if phase == 'alb-check':
        alb(driver)
        return
    if phase == 'e2e':
        raise Failure('AWS_E2E_ADAPTER_REQUIRED', 'Billing integration/9.1.4 currently uses Docker and mock payments')
    platform = driver.outputs('platform')
    with driver.kubernetes():
        if phase == 'foundation':
            foundation(driver, platform)
        elif phase == 'controller':
            controller(driver, platform)
        elif phase == 'secrets':
            from live_secrets import deliver_secrets
            from live_release import ca_material
            ca = ca_material(driver)
            driver.apply_bundle(dict(kind='List', items=[dict(apiVersion='v1', kind='ConfigMap',
                metadata=dict(namespace=PREFIX, name='rds-ca', labels={'oficina/environment': 'demo-a'}), data={'ca.pem': ca.decode()})]))
            deliver_secrets(driver)
        elif phase == 'data':
            from live_secrets import cleanup_temporary
            driver.apply_bundle(platform['manifests']['data'])
            for name in ('mongodb', 'rabbitmq'):
                driver.kubectl(['rollout', 'status', 'statefulset/' + name, '-n', PREFIX, '--timeout=300s'])
            driver.apply_bundle(platform['manifests']['bootstrap'])
            driver.verify_job(platform['manifests']['bootstrap'])
            cleanup_temporary(driver, 'bootstrap')
        elif phase == 'migrate':
            require(json.loads(platform['manifests']['migration'])['items'], 'MIGRATION_PLAN_REQUIRED')
            driver.apply_bundle(platform['manifests']['migration'])
            driver.verify_job(platform['manifests']['migration'])
            verify_databases(driver, platform)
        elif phase == 'apps':
            driver.apply_bundle(platform['manifests']['applications'])
            driver.apply_bundle(platform['manifests']['auth'])
            health(driver, platform)
        elif phase == 'ingress':
            manifest = driver.outputs('edge')['manifests']
            require(json.loads(manifest)['items'], 'INGRESS_PLAN_REQUIRED')
            driver.apply_bundle(manifest)
            driver.wait(lambda: bool(driver.owned_load_balancers()), 'ALB_RECONCILE_TIMEOUT')
            alb(driver)
        elif phase == 'observability':
            observability(driver, platform)
        elif phase == 'health':
            health(driver, platform)
        else:
            raise Failure('PHASE_REJECTED')
