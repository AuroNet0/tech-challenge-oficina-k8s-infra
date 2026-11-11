import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { availabilityEvidence, preflight, Report, root, verifyInfraInputs } from './control.mjs';

const config = JSON.parse(fs.readFileSync(path.join(root, 'phase4/environments/actions-first.json')));
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'phase4/environments/catalog.json')));
const runtime = JSON.parse(fs.readFileSync(path.join(root, 'phase4/environments/demo-a.runtime.json')));
const unapprovedRuntime = { ...structuredClone(runtime), approved: false, destroy_offline_verified: false,
  endpoint: { mode: 'broker', independent_canary_verified: false, private_creation_reviewed: false, recovery_reference: '' },
  preflight_authorization: {}, services: {}, mercado_pago_test_secret_arn: '', new_relic_secret_arn: '' };
const zones = ['a', 'b'].map((suffix, index) => ({ ZoneName: `us-east-1${suffix}`, ZoneId: `use1-az${index + 1}`,
  State: 'available', ZoneType: 'availability-zone', OptInStatus: 'opt-in-not-required' }));
const offerings = zones.map(zone => ({ InstanceType: 'm7i-flex.large', LocationType: 'availability-zone', Location: zone.ZoneName }));

async function simulate({ selected = [], backend = catalog.environments['demo-a'].backend_bucket,
  offered = offerings, scope = 'full', intent = 'apply', configuration = unapprovedRuntime, authorized = false,
  planAuthorized = true,
  respond = () => { throw new Error('AccessDenied password=private-fixture'); } } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'preflight-offline-'));
  const previousScope = process.env.PREFLIGHT_SCOPE;
  const previousRevision = process.env.GITHUB_SHA;
  const previousIntent = process.env.PREFLIGHT_INTENT;
  const previousExecution = process.env.PHASE4_EXECUTION_APPROVED;
  const previousPlan = process.env.PHASE4_PLAN_APPROVED;
  const calls = [];
  process.env.PREFLIGHT_SCOPE = scope;
  process.env.GITHUB_SHA = 'a'.repeat(40);
  process.env.PREFLIGHT_INTENT = intent;
  process.env.PHASE4_EXECUTION_APPROVED = authorized ? 'true' : 'false';
  process.env.PHASE4_PLAN_APPROVED = planAuthorized ? 'true' : 'false';
  try {
    const report = new Report('preflight', directory);
    await preflight({ ...config, execution_enabled: authorized, availability_zones: selected },
      { ...catalog.environments['demo-a'], enabled: authorized, backend_bucket: backend }, report,
      (service, operation, parameters = []) => {
        calls.push({ service, operation, parameters });
        if (service === 'sts') return { Account: config.account, Arn: `arn:aws:sts::${config.account}:assumed-role/tc-oficina-p4-demo-a-provision/offline` };
        if (operation === 'describe-availability-zones') return { AvailabilityZones: zones };
        if (operation === 'describe-instance-type-offerings') return { InstanceTypeOfferings: offered };
        return respond(service, operation, parameters);
      }, async () => { throw new Error('NETWORK_FORBIDDEN'); }, configuration);
    return { calls, data: report.data, status: report.finish(),
      summary: fs.readFileSync(path.join(directory, 'summary.md'), 'utf8') };
  } finally {
    if (previousScope === undefined) delete process.env.PREFLIGHT_SCOPE;
    else process.env.PREFLIGHT_SCOPE = previousScope;
    if (previousRevision === undefined) delete process.env.GITHUB_SHA;
    else process.env.GITHUB_SHA = previousRevision;
    for (const [name, value] of [['PREFLIGHT_INTENT', previousIntent], ['PHASE4_EXECUTION_APPROVED', previousExecution],
      ['PHASE4_PLAN_APPROVED', previousPlan]]) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

test('catalog metadata and infra inputs match demo-a while demo-b stays disabled', () => {
  assert.equal(catalog.environments['demo-a'].aws_account_id, '446517826146');
  assert.equal(catalog.environments['demo-a'].backend_bucket, 'tech-challenge-oficina-terraform-state-b1cfa326');
  assert.equal(typeof catalog.environments['demo-a'].enabled, 'boolean');
  assert.equal(typeof config.execution_enabled, 'boolean');
  assert.deepEqual(config.availability_zones, ['us-east-1a', 'us-east-1b']);
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

test('infra scope fails without a reviewed recovery strategy even when the cluster is absent', async () => {
  const result = await simulate({ scope: 'infra', respond: () => { throw new Error('ResourceNotFound'); } });
  const check = result.data.checks.find(check => check.phase === 'connectivity');
  assert.equal(check.status, 'FAIL');
  assert.equal(check.reason, 'EKS_RECOVERY_STRATEGY_NOT_REVIEWED');
  assert.equal(result.data.environment_ready, false);
  assert.equal(result.calls.some(call => call.service === 'lambda'), false);
  assert.equal(result.status, 1);
});

function reviewedInfra() {
  return { ...structuredClone(unapprovedRuntime), approved: true, destroy_offline_verified: true,
    endpoint: { ...unapprovedRuntime.endpoint, private_creation_reviewed: true, teardown_reviewed: true, recovery_reference: 'offline-recovery-review' },
    preflight_authorization: { account: config.account, region: config.region, scope: 'infra',
      reference: 'offline-free-and-backend-review', valid_until: Math.floor(Date.now() / 1000) + 3600,
      free_plan_creation_reviewed: true, backend_permissions_reviewed: true,
      free_services_reviewed: ['eks', 'rds', 'ec2', 'ecr', 'secretsmanager', 'logs', 'iam'] } };
}

function infrastructureResponses(service, operation, parameters) {
  if (operation === 'get-account-plan-state') return { accountPlanType: 'FREE', accountPlanStatus: 'ACTIVE' };
  if (operation === 'get-bucket-location') return { LocationConstraint: null };
  if (operation === 'get-bucket-versioning') return { Status: 'Enabled' };
  if (operation === 'get-bucket-encryption') return { ServerSideEncryptionConfiguration: { Rules: [{}] } };
  if (operation === 'get-public-access-block') return { PublicAccessBlockConfiguration: {
    BlockPublicAcls: true, IgnorePublicAcls: true, BlockPublicPolicy: true, RestrictPublicBuckets: true } };
  if (operation === 'list-objects-v2') return { Contents: [] };
  if (operation === 'get-object') throw new Error('NoSuchKey');
  if (operation === 'get-open-id-connect-provider') return { ClientIDList: ['sts.amazonaws.com'], Url: 'token.actions.githubusercontent.com' };
  if (operation === 'get-role') {
    const name = parameters[1];
    const purpose = name.split('demo-a-')[1];
    return { Role: { Arn: `arn:aws:iam::${config.account}:role/${name}`, AssumeRolePolicyDocument: { Statement: [{
      Effect: 'Allow', Action: 'sts:AssumeRoleWithWebIdentity',
      Principal: { Federated: `arn:aws:iam::${config.account}:oidc-provider/token.actions.githubusercontent.com` },
      Condition: { StringEquals: { 'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
        'token.actions.githubusercontent.com:sub': `repo:${config.repository}:environment:phase4-demo-a-${purpose}` } },
    }] } } };
  }
  if (operation === 'describe-cluster-versions') return { clusterVersions: [{ clusterVersion: '1.35', status: 'STANDARD_SUPPORT' }] };
  if (operation === 'describe-db-engine-versions') return { DBEngineVersions: [{ EngineVersion: '17.10', Status: 'available' }] };
  if (operation === 'describe-orderable-db-instance-options') return { OrderableDBInstanceOptions: [{}] };
  if (operation === 'describe-instance-types') return { InstanceTypes: [{ VCpuInfo: { DefaultVCpus: 2 } }] };
  if (operation === 'get-service-quota') return { Quota: { Value: 8 } };
  if (operation === 'describe-instances') return { Reservations: [] };
  if (operation === 'get-resources') return { ResourceTagMappingList: [] };
  if (operation === 'list-clusters') return { clusters: [] };
  if (operation === 'describe-db-instances') return { DBInstances: [] };
  if (operation === 'describe-vpcs') return { Vpcs: [] };
  if (operation === 'describe-repositories') return { repositories: [] };
  if (operation === 'describe-cluster') throw new Error('ResourceNotFound');
  throw new Error('UNEXPECTED_OFFLINE_CALL');
}

test('reviewed infra preflight succeeds without applications, external integrations or a nonexistent cluster canary', async () => {
  const result = await simulate({ selected: config.availability_zones, scope: 'infra', authorized: true,
    configuration: reviewedInfra(), respond: infrastructureResponses });
  assert.equal(result.status, 0, JSON.stringify(result.data.checks));
  assert.equal(result.data.scope, 'infra');
  assert.equal(result.data.environment_ready, false);
  assert.equal(result.data.checks.find(check => check.phase === 'connectivity').status, 'DEFERRED');
  const artifacts = result.data.checks.find(check => check.phase === 'artifacts');
  assert.equal(artifacts.status, 'DEFERRED');
  assert.equal(artifacts.reason, 'SERVICE_ARTIFACTS_REQUIRED_AFTER_INFRA');
  assert.equal(result.calls.some(call => call.service === 'lambda'), false);
});

test('infra with complete evidence still fails when execution is disabled', async () => {
  const result = await simulate({ selected: config.availability_zones, scope: 'infra',
    configuration: reviewedInfra(), respond: infrastructureResponses });
  assert.equal(result.data.checks.find(check => check.phase === 'execution-gate').reason, 'CATALOG_DISABLED');
  assert.equal(result.status, 1);
});

for (const missing of ['approval', 'credits-review', 'service', 'expiry', 'scope', 'backend-review', 'recovery']) {
  test(`infra preflight cannot replace missing ${missing} with a fictitious PASS`, async () => {
    const configuration = reviewedInfra();
    const review = configuration.preflight_authorization;
    if (missing === 'approval') configuration.approved = false;
    if (missing === 'credits-review') review.free_plan_creation_reviewed = false;
    if (missing === 'service') review.free_services_reviewed = review.free_services_reviewed.filter(service => service !== 'eks');
    if (missing === 'expiry') review.valid_until = 1;
    if (missing === 'scope') review.scope = 'full';
    if (missing === 'backend-review') review.backend_permissions_reviewed = false;
    if (missing === 'recovery') configuration.endpoint.private_creation_reviewed = false;
    const result = await simulate({ selected: config.availability_zones, scope: 'infra', authorized: true,
      configuration, respond: infrastructureResponses });
    assert.equal(result.status, 1);
    const phase = missing === 'recovery' ? 'connectivity' : missing === 'backend-review' ? 'backend-permissions' : 'service-entitlements';
    assert.equal(result.data.checks.find(check => check.phase === phase).status, 'FAIL');
    assert.equal(result.data.environment_ready, false);
  });
}

test('full scope keeps recovery and application requirements and rejects an infra-only FREE review', async () => {
  const result = await simulate({ selected: config.availability_zones, authorized: true,
    configuration: reviewedInfra(), respond: infrastructureResponses });
  for (const phase of ['service-entitlements', 'connectivity', 'artifacts']) {
    assert.equal(result.data.checks.find(check => check.phase === phase).status, 'FAIL');
  }
});

test('infra preserves real API failures and never defers a public cluster endpoint', async () => {
  for (const denied of [true, false]) {
    const result = await simulate({ selected: config.availability_zones, scope: 'infra', authorized: true,
      configuration: reviewedInfra(), respond: (service, operation, parameters) => {
        if (operation === 'describe-cluster') {
          if (denied) throw new Error('AccessDenied');
          return { cluster: { resourcesVpcConfig: { endpointPublicAccess: true, endpointPrivateAccess: true } } };
        }
        return infrastructureResponses(service, operation, parameters);
      } });
    const check = result.data.checks.find(check => check.phase === 'connectivity');
    assert.equal(check.status, 'FAIL');
    assert.equal(check.reason, denied ? 'AccessDenied' : 'INFRA_REQUIRES_CLOSED_ENDPOINT');
    assert.match(result.summary, new RegExp(check.reason));
    assert.equal(result.status, 1);
  }
});

test('infra inputs must match the reviewed runtime contract and preserve a private endpoint', () => {
  assert.doesNotThrow(() => verifyInfraInputs(config, runtime));
  for (const override of [{ node_count: 1 }, { availability_zones: ['us-east-1b', 'us-east-1a'] },
    { kubernetes_version: '1.34' }, { eks_public_access_cidrs: ['0.0.0.0/0'] }, { automation_roles: {} },
    { workload_permissions_boundaries: {} }]) {
    const changed = structuredClone(runtime);
    Object.assign(changed.terraform_inputs.infra, override);
    assert.throws(() => verifyInfraInputs(config, changed), /INFRA_INPUTS_NOT_REVIEWED/);
  }
});

test('unknown preflight scope is rejected before any AWS call', async () => {
  const result = await simulate({ scope: 'skip' });
  assert.equal(result.calls.length, 0);
  assert.equal(result.status, 1);
  assert.equal(result.data.checks[0].reason, 'PREFLIGHT_SCOPE_REJECTED');
});

test('read-only infra inspection ignores execution approvals and records future requirements as deferred', async () => {
  const result = await simulate({ selected: config.availability_zones, scope: 'infra', intent: 'inspect',
    respond: infrastructureResponses });
  assert.equal(result.status, 0, JSON.stringify(result.data.checks));
  for (const phase of ['execution-gate', 'service-entitlements', 'backend-permissions', 'connectivity', 'artifacts']) {
    assert.equal(result.data.checks.find(check => check.phase === phase).status, 'DEFERRED');
  }
  assert.equal(result.data.environment_ready, false);
  assert.equal(result.data.free_plan_documentation.account_configuration_eligibility_confirmed, false);
});

test('infra plan requires planning approval and backend review but never creation or recovery approvals', async () => {
  const configuration = structuredClone(unapprovedRuntime);
  configuration.preflight_authorization = reviewedInfra().preflight_authorization;
  delete configuration.preflight_authorization.free_plan_creation_reviewed;
  delete configuration.preflight_authorization.free_services_reviewed;
  const options = { selected: config.availability_zones, scope: 'infra', intent: 'plan', configuration,
    respond: infrastructureResponses };
  const result = await simulate(options);
  assert.equal(result.status, 0, JSON.stringify(result.data.checks));
  assert.equal(result.data.checks.find(check => check.phase === 'service-entitlements').status, 'DEFERRED');
  assert.equal(result.data.checks.find(check => check.phase === 'backend-permissions').status, 'PASS');
  const denied = await simulate({ ...options, planAuthorized: false });
  assert.equal(denied.status, 1);
  assert.equal(denied.data.checks.find(check => check.phase === 'execution-gate').reason, 'PLAN_NOT_AUTHORIZED');
  const unreviewed = structuredClone(configuration);
  unreviewed.preflight_authorization.backend_permissions_reviewed = false;
  assert.equal((await simulate({ ...options, configuration: unreviewed })).status, 1);
});
