import assert from 'node:assert/strict';
import fs from 'node:fs';
import { subject, validateLease } from './contracts.mjs';

const catalog = JSON.parse(fs.readFileSync(new URL('../../phase4/environments/catalog.json', import.meta.url)));
const now = new Date('2026-11-02T12:00:00Z');
const request = {
    environment_id: 'demo-a', account: '123456789012', observed_account: '123456789012', region: 'us-east-1', observed_region: 'us-east-1',
    cluster_arn: 'arn:aws:eks:us-east-1:123456789012:cluster/tc-oficina-p4-demo-a', observed_ip: '8.8.8.8', cidr: '8.8.8.8/32',
    lease_id: 'synthetic-lease-12345', expires_at: '2026-11-02T12:15:00Z'
};
let checks = 0;
for (const environment of ['demo-a', 'demo-b']) {
    assert.equal(catalog.environments[environment].enabled, false);
    assert.ok(subject(environment, 'deploy').endsWith(`:environment:phase4-${environment}-deploy`));
    assert.ok(!subject(environment, 'destroy').includes('ref:refs/heads'));
    checks += 3;
}
assert.throws(() => subject('demo-c', 'deploy'));
assert.throws(() => subject('demo-a', 'administrator'));
assert.throws(() => validateLease(catalog, request, now));
checks += 3;
const synthetic = structuredClone(catalog);
synthetic.environments['demo-a'] = { ...synthetic.environments['demo-a'], enabled: true, aws_account_id: request.account };
assert.deepEqual(validateLease(synthetic, request, now), { request_valid: true, dispatch_allowed: false, reason: 'DURABLE_RECOVERY_NOT_IMPLEMENTED' });
checks++;
for (const change of [
    { environment_id: 'demo-b' }, { observed_account: '210987654321' }, { observed_region: 'us-west-2' },
    { cluster_arn: request.cluster_arn.replace('demo-a', 'demo-b') }, { cidr: '0.0.0.0/0' }, { cidr: '8.8.8.8/24' },
    { observed_ip: '8.8.4.4' }, { observed_ip: '10.0.0.1', cidr: '10.0.0.1/32' }, { observed_ip: '127.0.0.1', cidr: '127.0.0.1/32' },
    { observed_ip: '::1', cidr: '::1/128' }, { expires_at: '2026-11-02T11:59:00Z' }, { expires_at: '2026-11-02T13:00:00Z' },
    { lease_id: 'arbitrary/other-environment' }
]) {
    assert.throws(() => validateLease(synthetic, { ...request, ...change }, now));
    checks++;
}
assert.equal(catalog.environments['demo-a'].enabled, false);
console.log(`Identity/OIDC/lease contracts: ${checks} offline checks passed; endpoint dispatch remains blocked.`);
