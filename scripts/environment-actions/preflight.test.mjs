import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { availabilityEvidence, preflight, Report, root } from './control.mjs';

const config = JSON.parse(fs.readFileSync(path.join(root, 'phase4/environments/actions-first.json')));
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'phase4/environments/catalog.json')));
const zones = ['a', 'b'].map((suffix, index) => ({ ZoneName: `us-east-1${suffix}`, ZoneId: `use1-az${index + 1}`,
  State: 'available', ZoneType: 'availability-zone', OptInStatus: 'opt-in-not-required' }));
const offerings = zones.map(zone => ({ InstanceType: 'm7i-flex.large', LocationType: 'availability-zone', Location: zone.ZoneName }));

async function simulate({ selected = [], backend = catalog.environments['demo-a'].backend_bucket,
  offered = offerings, scope = 'full', respond = () => { throw new Error('AccessDenied password=private-fixture'); } } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'preflight-offline-'));
  const previousScope = process.env.PREFLIGHT_SCOPE;
  const calls = [];
  process.env.PREFLIGHT_SCOPE = scope;
  try {
    const report = new Report('preflight', directory);
    await preflight({ ...config, availability_zones: selected }, { ...catalog.environments['demo-a'], backend_bucket: backend }, report,
      (service, operation, parameters = []) => {
        calls.push({ service, operation, parameters });
        if (service === 'sts') return { Account: config.account, Arn: `arn:aws:sts::${config.account}:assumed-role/tc-oficina-p4-demo-a-provision/offline` };
        if (operation === 'describe-availability-zones') return { AvailabilityZones: zones };
        if (operation === 'describe-instance-type-offerings') return { InstanceTypeOfferings: offered };
        return respond(service, operation, parameters);
      }, async () => { throw new Error('NETWORK_FORBIDDEN'); });
    return { calls, data: report.data, status: report.finish(),
      summary: fs.readFileSync(path.join(directory, 'summary.md'), 'utf8') };
  } finally {
    if (previousScope === undefined) delete process.env.PREFLIGHT_SCOPE;
    else process.env.PREFLIGHT_SCOPE = previousScope;
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

test('catalog contains confirmed demo-a metadata while all execution gates and demo-b stay disabled', () => {
  assert.equal(catalog.environments['demo-a'].aws_account_id, '446517826146');
  assert.equal(catalog.environments['demo-a'].backend_bucket, 'tech-challenge-oficina-terraform-state-b1cfa326');
  assert.equal(catalog.environments['demo-a'].enabled, false);
  assert.equal(config.execution_enabled, false);
  assert.deepEqual(config.availability_zones, []);
  assert.deepEqual(catalog.environments['demo-b'], { enabled: false, aws_account_id: null, aws_region: 'us-east-1',
    project_name: 'oficina', resource_prefix: 'tc', owner: 'tech-challenge-oficina', backend_bucket: null,
    state_keys: { infra: 'phase4/environments/demo-b/infra.tfstate', platform: 'phase4/environments/demo-b/platform.tfstate',
      edge: 'phase4/environments/demo-b/edge.tfstate' } });
});

test('discovery records only available standard AZs with matching instance type offerings', () => {
  const evidence = availabilityEvidence(config, (service, operation) => operation === 'describe-availability-zones'
    ? { AvailabilityZones: [...zones, { ...zones[0], ZoneName: 'us-east-1c', State: 'unavailable' },
      { ...zones[0], ZoneName: 'us-east-1d', OptInStatus: 'not-opted-in' },
      { ...zones[0], ZoneName: 'us-east-1e', ZoneType: 'local-zone' },
      { ...zones[0], ZoneName: '<script>private-fixture</script>' }] }
    : { InstanceTypeOfferings: [offerings[0], { ...offerings[1], InstanceType: 'another.type' }] });
  assert.deepEqual(evidence.available_zones, [{ name: 'us-east-1a', id: 'use1-az1' }, { name: 'us-east-1b', id: 'use1-az2' }]);
  assert.deepEqual(evidence.candidate_zones, ['us-east-1a']);
  assert.doesNotMatch(JSON.stringify(evidence), /private-fixture/);
});

test('incomplete AZ selection still discovers candidates without turning mandatory checks into PASS', async () => {
  const result = await simulate();
  assert.deepEqual(result.data.availability.candidate_zones, ['us-east-1a', 'us-east-1b']);
  assert.match(result.summary, /use1-az1/);
  assert.match(result.summary, /No zones selected automatically/);
  assert.equal(result.data.checks.find(check => check.phase === 'configuration').status, 'PASS');
  assert.equal(result.data.checks.find(check => check.phase === 'execution-gate').reason, 'CATALOG_DISABLED');
  for (const phase of ['network', 'capacity']) assert.equal(result.data.checks.find(check => check.phase === phase).reason, 'TWO_AZS_REQUIRED');
  assert.equal(result.data.checks.find(check => check.phase === 'connectivity').reason, 'INDEPENDENT_RECOVERY_CANARY_REQUIRED');
  assert.equal(result.data.environment_ready, false);
  assert.equal(result.status, 1);
});

test('instance type must be offered in both selected AZs before quota checks', async () => {
  const result = await simulate({ selected: ['us-east-1a', 'us-east-1b'], offered: [offerings[0]] });
  assert.equal(result.data.checks.find(check => check.phase === 'network').status, 'PASS');
  assert.equal(result.data.checks.find(check => check.phase === 'capacity').reason, 'NODE_AZ_OFFERING_UNAVAILABLE');
  assert.equal(result.calls.some(call => call.operation === 'get-service-quota'), false);
});

test('two supported AZs still require vCPU quota and do not bypass the execution or recovery gates', async () => {
  const result = await simulate({ selected: ['us-east-1a', 'us-east-1b'], respond: (service, operation) => {
    if (operation === 'describe-instance-types') return { InstanceTypes: [{ VCpuInfo: { DefaultVCpus: 2 } }] };
    if (operation === 'get-service-quota') return { Quota: { Value: 4 } };
    if (operation === 'describe-instances') return { Reservations: [] };
    throw new Error('AccessDenied');
  } });
  assert.equal(result.data.checks.find(check => check.phase === 'capacity').status, 'PASS');
  assert.equal(result.calls.some(call => call.operation === 'get-service-quota'), true);
  assert.equal(result.data.checks.find(check => check.phase === 'execution-gate').status, 'FAIL');
  assert.equal(result.data.checks.find(check => check.phase === 'connectivity').reason, 'INDEPENDENT_RECOVERY_CANARY_REQUIRED');
  assert.equal(result.status, 1);
});

test('duplicate AZ selections cannot satisfy network or capacity checks', async () => {
  const result = await simulate({ selected: ['us-east-1a', 'us-east-1a'] });
  for (const phase of ['network', 'capacity']) assert.equal(result.data.checks.find(check => check.phase === phase).reason, 'TWO_AZS_REQUIRED');
  assert.equal(result.calls.some(call => call.operation === 'get-service-quota'), false);
});

test('a missing backend never produces an AWS request with bucket null', async () => {
  const result = await simulate({ backend: null });
  assert.equal(result.data.checks.find(check => check.phase === 'configuration').reason, 'CATALOG_INCOMPLETE');
  assert.equal(result.data.checks.find(check => check.phase === 'inventory').reason, 'BACKEND_NOT_CONFIGURED');
  assert.equal(result.calls.some(call => call.service === 's3api'), false);
});

test('inventory denial records the exact call and required object permission without raw exception text', async () => {
  const result = await simulate();
  const check = result.data.checks.find(check => check.phase === 'inventory');
  assert.equal(check.reason, 'AccessDenied');
  assert.deepEqual(check.aws_call, { service: 's3api', operation: 'get-object' });
  assert.deepEqual(check.required_permissions, [{ action: 's3:GetObject',
    resource: 'arn:aws:s3:::tech-challenge-oficina-terraform-state-b1cfa326/phase4/environments/demo-a/operations/inventory.json' }]);
  assert.match(result.summary, /s3api get-object/);
  assert.match(result.summary, /s3:GetObject/);
  assert.doesNotMatch(JSON.stringify(result.data) + result.summary, /private-fixture/);
});

test('a missing inventory object does not hide a subsequent tagging API denial', async () => {
  const result = await simulate({ respond: (service, operation) => {
    if (operation === 'get-object') throw new Error('NoSuchKey');
    throw new Error('AccessDenied');
  } });
  const check = result.data.checks.find(check => check.phase === 'inventory');
  assert.equal(check.reason, 'AccessDenied');
  assert.deepEqual(check.aws_call, { service: 'resourcegroupstaggingapi', operation: 'get-resources' });
});

test('infra scope keeps post-EKS connectivity deferred and never reports a ready environment', async () => {
  const result = await simulate({ scope: 'infra', respond: () => { throw new Error('ResourceNotFound'); } });
  const check = result.data.checks.find(check => check.phase === 'connectivity');
  assert.equal(check.status, 'DEFERRED');
  assert.equal(check.reason, 'CONTROL_PLANE_ONLY_NO_KUBERNETES_ACCESS');
  assert.equal(result.data.environment_ready, false);
  assert.equal(result.calls.some(call => call.service === 'lambda'), false);
  assert.equal(result.status, 1);
});
