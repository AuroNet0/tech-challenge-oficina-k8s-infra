import assert from 'node:assert/strict';
import fs from 'node:fs';

const events = fs.readFileSync(process.argv[2], 'utf8').replace(/^\uFEFF/, '').trim().split(/\r?\n/).map(line => JSON.parse(line));
assert.ok(events.some(event => event.type === 'test_summary' && event.test_summary.failed === 0));
const plan = events.find(event => event.type === 'test_plan' && event['@testrun'] === 'business_ready').test_plan;
const bundle = JSON.parse(plan.output_changes.manifests.after);
const allowlist = plan.output_changes.route_allowlist.after;
const ingress = bundle.items.filter(item => item.kind === 'Ingress');
assert.equal(ingress.length, 8);
assert.equal(bundle.items.filter(item => item.kind === 'NetworkPolicy').length, 3);
assert.equal(bundle.items.filter(item => item.kind === 'IngressClass').length, 1);
const identities = new Set();
for (const item of bundle.items) {
    assert.equal(item.metadata.labels['oficina/environment'], 'demo-a');
    const identity = `${item.kind}/${item.metadata.name}`;
    assert.ok(!identities.has(identity));
    identities.add(identity);
    if (item.kind !== 'IngressClass') assert.equal(item.metadata.namespace, 'tc-oficina-p4-demo-a');
}
const routes = Object.keys(allowlist.business);
for (const [route, service] of Object.entries(allowlist.business)) {
    const [method, path] = route.split(' ');
    const sample = path.replace(/\{[^}]+\}/g, 'synthetic-id');
    const matches = ingress.filter(item => {
        const conditions = JSON.parse(item.metadata.annotations[`alb.ingress.kubernetes.io/conditions.${service}-service`] || '[]');
        return item.spec.rules[0].http.paths.some(backend => backend.backend.service.name === `${service}-service`
            && new RegExp(`^${backend.path.replaceAll('*', '.*')}$`).test(sample))
            && conditions.some(condition => condition.httpRequestMethodConfig?.values.includes(method));
    });
    assert.equal(matches.length, 1, route);
}
for (const item of ingress) {
    const annotations = item.metadata.annotations;
    assert.equal(annotations['alb.ingress.kubernetes.io/scheme'], 'internal');
    assert.equal(annotations['alb.ingress.kubernetes.io/target-type'], 'ip');
    assert.equal(annotations['alb.ingress.kubernetes.io/backend-protocol'], 'HTTPS');
    assert.equal(annotations['alb.ingress.kubernetes.io/healthcheck-path'], '/actuator/health/readiness');
    for (const rule of item.spec.rules[0].http.paths) {
        assert.equal(rule.pathType, 'ImplementationSpecific');
        assert.equal(rule.backend.service.port.number, 8443);
        const expression = new RegExp(`^${rule.path.replaceAll('*', '.*')}$`);
        for (const forbidden of ['/internal/technical-authorization', '/internal/v1/identity/client-access/check', '/actuator/health', '/swagger-ui/index.html']) {
            assert.equal(expression.test(forbidden), false, forbidden);
        }
    }
}
assert.equal(routes.length, 40);
const resources = plan.resource_changes.map(change => change.change.after).filter(Boolean);
const lambda = resources.find(resource => resource.function_name === 'tc-oficina-p4-demo-a-auth' && resource.runtime);
assert.ok(lambda);
assert.ok(!Object.keys(lambda.environment[0].variables).includes('AUTH_DB_PASSWORD'));
assert.equal(lambda.environment[0].variables.AUTH_JWT_ISSUER, plan.output_changes.jwt_contract.after.issuer);
const permissions = plan.resource_changes.filter(change => change.type === 'aws_lambda_permission');
assert.equal(permissions.length, 2);
for (const permission of permissions) {
    assert.ok(permission.change.after.source_arn.includes('/$default/'));
    assert.ok(!permission.change.after.source_arn.includes('*'));
}
if (process.argv[3]) fs.writeFileSync(process.argv[3], JSON.stringify(bundle));
console.log(`Edge: ${routes.length + 2} routes, ${bundle.items.length} Kubernetes objects; isolation/TLS/private paths verified offline.`);
