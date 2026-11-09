import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const [eventsFile, destination] = process.argv.slice(2);
assert.ok(destination, 'Usage: node check-delivery-contract.mjs <terraform-test-jsonl> <public-fixture.json>');
const events = fs.readFileSync(eventsFile, 'utf8').replace(/^\uFEFF/, '').trim().split(/\r?\n/).map(line => JSON.parse(line));
assert.ok(events.some(event => event.type === 'test_summary' && event.test_summary.failed === 0));
const outputs = events.find(event => event.type === 'test_plan' && event['@testrun'] === 'render_isolated_platform').test_plan.output_changes;
const delivery = outputs.secret_delivery_contract.after;
assert.equal(delivery.schema_version, 1);
assert.equal(delivery.live_transport_enabled, false);
assert.equal(delivery.ca_projected, false);
assert.equal(delivery.canonical_prefix, '/oficina/phase4/demo-a/');
assert.deepEqual(Object.keys(delivery.secrets).sort(), Object.keys(outputs.secret_contract.after).sort());
for (const service of ['os-service', 'execution-service', 'billing-service']) {
    assert.ok(delivery.secrets[`${service}-tls`].keys.includes('java-options'));
    assert.ok(delivery.secrets[`${service}-tls`].keys.includes('inbound-truststore.p12'));
}
assert.ok(!delivery.secrets['auth-internal-tls'].keys.includes('java-options'));
const infra = JSON.parse(fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixture.json'), 'utf8'));
const secretContract = outputs.secret_contract.after;
infra.rds.master_secret_arn += '-ABC123';
secretContract['postgres-bootstrap-admin'].source = infra.rds.master_secret_arn;
fs.writeFileSync(destination, JSON.stringify({ fixture_only: true, infra_contract: infra, secret_contract: secretContract,
    secret_delivery_contract: delivery }, null, 2) + '\n');
console.log(`PASS: ${Object.keys(delivery.secrets).length} secret metadata contracts; public fixture only.`);
