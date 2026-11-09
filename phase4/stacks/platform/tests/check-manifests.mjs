import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const inputPath = process.argv[2];
assert.ok(inputPath, 'Usage: node check-manifests.mjs <terraform-test-jsonl> [fixture-export-directory]');
const events = fs.readFileSync(inputPath, 'utf8').replace(/^\uFEFF/, '').trim().split(/\r?\n/).map(line => JSON.parse(line));
const rendered = events.find(event => event.type === 'test_plan' && event['@testrun'] === 'render_isolated_platform');
assert.ok(rendered, 'A successful render test with verbose JSON output is required');
assert.ok(events.some(event => event.type === 'test_summary' && event.test_summary.failed === 0));
const changes = rendered.test_plan.output_changes;
const migrationRun = events.find(event => event.type === 'test_plan' && event['@testrun'] === 'render_controlled_migration');
const migrationBundle = JSON.parse(migrationRun.test_plan.output_changes.manifests.after.migration);
assert.equal(migrationBundle.items[0].spec.completions, 1);
const migrationPod = migrationBundle.items[0].spec.template.spec;
assert.equal(migrationPod.containers[0].env.find(environment => environment.name === 'AUTH_DB_USER').valueFrom.secretKeyRef.name, 'auth-migration-postgres');
assert.ok(!migrationPod.containers[0].env.some(environment => environment.name === 'AUTH_DB_SSLROOTCERT'));
assert.deepEqual(migrationPod.volumes[0].configMap.items, [{ key: 'ca.pem', path: 'root.crt' }]);
const bundles = Object.fromEntries(Object.entries(changes.manifests.after).map(([name, text]) => [name, JSON.parse(text)]));
const resources = Object.values(bundles).flatMap(bundle => {
    assert.equal(bundle.apiVersion, 'v1');
    assert.equal(bundle.kind, 'List');
    assert.ok(Array.isArray(bundle.items));
    return bundle.items;
});
const namespace = 'tc-oficina-p4-demo-a';
const knownSecrets = changes.secret_contract.after;
const knownConfigMaps = new Set([
    ...resources.filter(resource => resource.kind === 'ConfigMap').map(resource => resource.metadata.name),
    ...Object.keys(changes.external_configmaps.after)
]);
const serviceAccounts = new Set(resources.filter(resource => resource.kind === 'ServiceAccount').map(resource => resource.metadata.name));
const identities = new Set();
let requestsCpu = 0;
let requestsMemory = 0;
for (const resource of resources) {
    assert.ok(resource.apiVersion && resource.kind && resource.metadata?.name);
    const identity = `${resource.kind}/${resource.metadata.namespace || ''}/${resource.metadata.name}`;
    assert.ok(!identities.has(identity), `Duplicate resource: ${identity}`);
    identities.add(identity);
    assert.equal(resource.metadata.labels['oficina/environment'], 'demo-a');
    assert.ok(!['Secret', 'Ingress', 'ClusterRoleBinding', 'RoleBinding'].includes(resource.kind));
    if (!['Namespace', 'StorageClass'].includes(resource.kind)) assert.equal(resource.metadata.namespace, namespace);
    if (resource.kind === 'Service') {
        assert.equal(resource.spec.type, 'ClusterIP');
        assert.ok(resource.spec.ports.every(port => port.port !== 15672));
    }
    if (resource.kind === 'ServiceAccount') assert.equal(resource.automountServiceAccountToken, false);
    if (!['Deployment', 'StatefulSet', 'Job'].includes(resource.kind)) continue;
    const pod = resource.spec.template.spec;
    assert.ok(serviceAccounts.has(pod.serviceAccountName));
    assert.equal(pod.automountServiceAccountToken, false);
    assert.equal(pod.securityContext.runAsNonRoot, true);
    assert.ok(pod.securityContext.runAsUser > 0);
    assert.equal(pod.securityContext.seccompProfile.type, 'RuntimeDefault');
    assert.ok(!pod.hostNetwork && !pod.hostPID && !pod.hostIPC);
    assert.ok(!pod.volumes.some(volume => volume.hostPath));
    for (const volume of pod.volumes) {
        if (volume.secret) assert.ok(knownSecrets[volume.secret.secretName], `Unknown volume secret: ${volume.secret.secretName}`);
        if (volume.configMap) assert.ok(knownConfigMaps.has(volume.configMap.name), `Unknown ConfigMap: ${volume.configMap.name}`);
    }
    for (const container of [...(pod.initContainers || []), ...pod.containers]) {
        assert.match(container.image, /@sha256:[0-9a-f]{64}$/);
        assert.equal(container.securityContext.allowPrivilegeEscalation, false);
        assert.equal(container.securityContext.readOnlyRootFilesystem, true);
        assert.deepEqual(container.securityContext.capabilities.drop, ['ALL']);
        assert.ok(container.resources.requests.cpu && container.resources.requests.memory);
        assert.ok(container.resources.limits.cpu && container.resources.limits.memory);
        for (const source of container.envFrom || []) {
            if (source.configMapRef) assert.ok(knownConfigMaps.has(source.configMapRef.name));
        }
        for (const environment of container.env || []) {
            if (environment.valueFrom?.secretKeyRef) {
                const reference = environment.valueFrom.secretKeyRef;
                assert.ok(knownSecrets[reference.name]?.keys.includes(reference.key), `Unknown secret reference: ${reference.name}/${reference.key}`);
            }
        }
    }
    if (resource.kind === 'Job') continue;
    for (const container of pod.containers) {
        assert.ok(container.startupProbe && container.readinessProbe && container.livenessProbe);
        requestsCpu += Number(container.resources.requests.cpu.replace('m', ''));
        requestsMemory += Number(container.resources.requests.memory.replace('Mi', ''));
    }
    if (resource.kind === 'Deployment') {
        assert.equal(resource.spec.replicas, 0);
        assert.equal(resource.spec.strategy.type, 'Recreate');
        if (resource.metadata.name === 'auth-internal') {
            assert.equal(pod.containers[0].command, undefined);
            assert.equal(pod.containers[0].args, undefined);
            assert.equal(pod.containers[0].livenessProbe.httpGet.path, '/health/live');
            assert.equal(pod.containers[0].readinessProbe.httpGet.path, '/health/ready');
            assert.equal(pod.containers[0].readinessProbe.httpGet.scheme, 'HTTPS');
            assert.equal(pod.securityContext.runAsUser, 10001);
            assert.equal(pod.terminationGracePeriodSeconds, 45);
        } else {
            assert.ok(pod.containers[0].command.includes('@/tls/java-options'));
            assert.ok(!pod.containers[0].command.some(argument => argument.includes('Password=')));
        }
        assert.ok(!pod.containers[0].env.some(environment => environment.name === 'JAVA_TOOL_OPTIONS'));
    }
    if (resource.kind === 'StatefulSet') {
        assert.equal(resource.spec.replicas, 1);
        assert.equal(resource.spec.persistentVolumeClaimRetentionPolicy.whenDeleted, 'Delete');
        assert.equal(resource.spec.volumeClaimTemplates[0].spec.storageClassName, `${namespace}-gp3`);
    }
}
assert.equal(changes.auth_internal_contract.after.runtime_available, true);
assert.equal(changes.auth_internal_contract.after.image_verified, false);
assert.equal(bundles.migration.items.length, 0);
const authConfig = resources.find(resource => resource.kind === 'ConfigMap' && resource.metadata.name === 'auth-internal-config').data;
for (const service of ['os-service', 'execution-service', 'billing-service']) {
    const config = resources.find(resource => resource.kind === 'ConfigMap' && resource.metadata.name === `${service}-config`).data;
    assert.equal(config.SERVER_SSL_TRUST_STORE, 'file:/tls/inbound-truststore.p12');
}
const authPod = resources.find(resource => resource.kind === 'Deployment' && resource.metadata.name === 'auth-internal').spec.template.spec;
const authInputs = new Set([...Object.keys(authConfig), ...authPod.containers[0].env.map(environment => environment.name)]);
for (const input of ['AUTH_INTERNAL_PORT', 'AUTH_INTERNAL_KEYSTORE', 'AUTH_INTERNAL_TRUSTSTORE',
    'AUTH_INTERNAL_KEYSTORE_PASSWORD', 'AUTH_INTERNAL_TRUSTSTORE_PASSWORD', 'AUTH_INTERNAL_OS_SUBJECT',
    'AUTH_INTERNAL_PROBE_HOST', 'AUTH_DB_HOST', 'AUTH_DB_PORT', 'AUTH_DB_NAME', 'AUTH_DB_USER', 'AUTH_DB_PASSWORD',
    'AUTH_DB_SSLMODE', 'AUTH_DB_SSLROOTCERT', 'AUTH_INTERNAL_DB_POOL_SIZE', 'AUTH_JWT_ISSUER', 'AUTH_JWT_AUDIENCE', 'AUTH_JWKS_URI']) {
    assert.ok(authInputs.has(input), `Missing productive Auth input: ${input}`);
}
assert.equal(authConfig.AUTH_INTERNAL_OS_SUBJECT, 'CN=os-service');
assert.equal(authConfig.AUTH_DB_SSLMODE, 'verify-full');
assert.equal(authConfig.AUTH_INTERNAL_PROBE_HOST, `auth-internal.${namespace}.svc.cluster.local`);
assert.equal(authConfig.AUTH_JWKS_URI, 'https://auth.test.example.org/.well-known/jwks.json');
assert.ok(!Object.keys(authConfig).some(key => /PASSWORD|PRIVATE|KEY_PROVIDER|KEY_SECRET/.test(key)));
assert.equal(changes.edge_contract.after.listener_ready, false);
assert.equal(resources.filter(resource => resource.kind === 'Deployment').length, 4);
assert.equal(resources.filter(resource => resource.kind === 'StatefulSet').length, 2);
assert.ok(resources.some(resource => resource.kind === 'NetworkPolicy' && resource.metadata.name === 'default-deny'));
assert.ok(!resources.some(resource => resource.kind === 'NetworkPolicy' && resource.metadata.name === 'execution-service-postgres'));

const scriptsDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../scripts');
const initialization = fs.readFileSync(path.join(scriptsDirectory, 'mongo-init.js'), 'utf8');
const readiness = fs.readFileSync(path.join(scriptsDirectory, 'mongo-ready.js'), 'utf8');
function mongoFixture() {
    const state = { initialized: false, primary: true, root: false, application: null, creations: 0, wrongRoot: false };
    const environment = { MONGO_ROOT_USERNAME: 'test-root', MONGO_ROOT_PASSWORD: 'test-only-random-root', MONGO_USERNAME: 'test-app', MONGO_PASSWORD: 'test-only-random-app', MONGO_DATABASE: 'oficina_execution', MONGO_MEMBER: `mongodb-0.mongodb.${namespace}.svc.cluster.local:27017` };
    const context = {
        process: { env: environment }, print() {}, quit(code) {
            context.mongoExit ??= code;
            throw { exitCode: context.mongoExit };
        },
        db: {
            hello: () => ({ isWritablePrimary: state.primary }),
            getSiblingDB(database) {
                if (database === 'admin') return {
                    auth() { if (!state.root || state.wrongRoot) throw new Error('authentication failed'); return true; },
                    createUser() { if (state.root) throw new Error('unauthorized'); state.root = true; }
                };
                return {
                    getUser: () => state.application,
                    createUser(user) { state.application = user; state.creations++; },
                    auth: () => Boolean(state.application),
                    runCommand: () => ({ isWritablePrimary: state.primary, setName: 'rs0' })
                };
            }
        },
        rs: {
            initiate() { if (state.initialized) throw { code: 23 }; state.initialized = true; },
            conf: () => ({ _id: 'rs0', members: [{ host: environment.MONGO_MEMBER }] })
        }
    };
    return { state, context };
}
function execute(source, context) {
    delete context.mongoExit;
    try { vm.runInNewContext(source, context, { timeout: 1000 }); } catch (failure) {
        if (failure.exitCode !== undefined) return failure.exitCode;
        throw failure;
    }
    throw new Error('MongoDB script did not return an exit code');
}
const fresh = mongoFixture();
assert.equal(execute(initialization, fresh.context), 0);
assert.equal(execute(initialization, fresh.context), 0);
assert.equal(fresh.state.creations, 1);
assert.equal(execute(readiness, fresh.context), 0);
fresh.state.primary = false;
assert.equal(execute(readiness, fresh.context), 1);
const invalidIdentity = mongoFixture();
invalidIdentity.state.root = true;
invalidIdentity.state.initialized = true;
invalidIdentity.state.wrongRoot = true;
assert.equal(execute(initialization, invalidIdentity.context), 1);
const wrongRole = mongoFixture();
wrongRole.state.root = true;
wrongRole.state.initialized = true;
wrongRole.state.application = { roles: [{ role: 'root', db: 'admin' }] };
assert.equal(execute(initialization, wrongRole.context), 1);

if (process.argv[3]) {
    fs.mkdirSync(process.argv[3], { recursive: true });
    for (const [name, bundle] of Object.entries(bundles)) {
        fs.writeFileSync(path.join(process.argv[3], `${name}.fixture.json`), JSON.stringify(bundle, null, 2) + '\n');
    }
    fs.writeFileSync(path.join(process.argv[3], 'TEST-FIXTURES-ONLY.txt'), 'Synthetic images, account and URLs. Never deploy these test fixtures.\n');
    fs.writeFileSync(path.join(process.argv[3], 'migration-enabled.fixture.json'), JSON.stringify(migrationBundle, null, 2) + '\n');
}
console.log(`PASS: ${resources.length} rendered Kubernetes objects; security, references, images and six MongoDB script scenarios.`);
console.log(`One replica per service including Auth plus MongoDB/RabbitMQ: requests ${requestsCpu}m CPU / ${requestsMemory}Mi RAM; excludes controllers/observability/system.`);
