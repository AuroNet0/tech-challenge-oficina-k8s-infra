import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { aws, context, errorCode, nodeChildArns, operationalGate, preflight, Report, root } from './control.mjs';
import { collectKubernetes, command, logSignatures, summarizeKubernetes, terraformValidation } from './diagnostics.mjs';
import { reviewPlan, validateInputs } from './bootstrap.mjs';

const config = JSON.parse(fs.readFileSync(path.join(root, 'phase4/environments/actions-first.json')));
const environment = { GITHUB_REPOSITORY: config.repository, GITHUB_REF: 'refs/heads/homolog', GITHUB_EVENT_NAME: 'workflow_dispatch' };
const catalog = { enabled: true, aws_account_id: config.account, aws_region: config.region, backend_bucket: 'approved-backend' };

async function withReport(operation, action) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'phase4-actions-'));
  try { await action(new Report(operation, directory)); }
  finally { fs.rmSync(directory, { recursive: true, force: true }); }
}

test('context accepts only expected repository, dispatch and homolog', () => {
  context(config, environment);
  for (const override of [{ GITHUB_REF: 'refs/heads/main' }, { GITHUB_EVENT_NAME: 'push' }, { GITHUB_REPOSITORY: 'foreign/repository' }]) {
    assert.throws(() => context(config, { ...environment, ...override }));
  }
});

for (const operation of ['up', 'destroy']) {
  test(`${operation} enforces confirmation and authorization; Destroy permits disabled environment recovery`, () => {
    assert.throws(() => operationalGate(operation, 'demo-b', config, catalog, environment), /CONFIRMATION_REJECTED/);
    assert.throws(() => operationalGate(operation, `${operation.toUpperCase()} demo-a`, config, catalog, environment), /EXECUTION_DISABLED/);
    const approved = { ...environment, PHASE4_EXECUTION_APPROVED: 'true' };
    operationalGate(operation, `${operation.toUpperCase()} demo-a`, { ...config, execution_enabled: true }, catalog, approved);
    if (operation === 'up') assert.throws(() => operationalGate(operation, 'UP demo-a', { ...config, execution_enabled: true }, { ...catalog, enabled: false }, approved), /CATALOG_DISABLED/);
    else operationalGate('destroy', 'DESTROY demo-a', { ...config, execution_enabled: true }, { ...catalog, enabled: false }, approved);
  });
}

test('AWS read-only allowlist rejects mutations before starting a process', () => {
  let executed = false;
  for (const [service, operation] of [['eks', 'update-cluster-config'], ['s3api', 'put-object'], ['iam', 'create-role']]) {
    assert.throws(() => aws(service, operation, [], () => { executed = true; }), /ALLOWLIST_REJECTED/);
  }
  assert.equal(executed, false);
  assert.throws(() => aws('lambda', 'invoke', ['--payload', '{"lease_id":"unsafe"}'], () => { executed = true; }), /ALLOWLIST_REJECTED/);
  assert.equal(executed, false);
});

test('resumed preflight recognizes EKS node children from actual ASG membership and rejects a different VPC', () => {
  const inventory = { resources: { infra: [{ type: 'aws_vpc', id: 'vpc-owned' }, { type: 'aws_eks_cluster' }] } };
  let vpc = 'vpc-owned';
  const call = service => {
    if (service === 'eks') return { nodegroup: { clusterName: 'tc-oficina-p4-demo-a', tags: { EnvironmentId: 'demo-a' }, resources: { autoScalingGroups: [{ name: 'owned-group' }] } } };
    if (service === 'autoscaling') return { AutoScalingGroups: [{ Instances: [{ InstanceId: 'i-123' }] }] };
    return { Reservations: [{ Instances: [{ InstanceId: 'i-123', VpcId: vpc,
      BlockDeviceMappings: [{ Ebs: { VolumeId: 'vol-123' } }], NetworkInterfaces: [{ NetworkInterfaceId: 'eni-123' }] }] }] };
  };
  assert.ok(nodeChildArns(inventory, call).some(arn => arn.endsWith('volume/vol-123')));
  vpc = 'vpc-foreign';
  assert.throws(() => nodeChildArns(inventory, call), /NODE_VPC_MISMATCH/);
});

test('AWS errors distinguish denied, missing and unsupported without exposing payloads', () => {
  for (const code of ['AccessDenied', 'NoSuchBucket', 'ResourceNotFound', 'Unsupported']) {
    assert.throws(() => aws('sts', 'get-caller-identity', [], () => ({ status: 1, stderr: `${code}: password=private JWT=secret` })), error => error.message === code);
  }
});

test('AWS region and paging are explicit', () => {
  aws('sts', 'get-caller-identity', [], (executable, argumentsList) => {
    assert.equal(executable, 'aws');
    assert.equal(argumentsList[argumentsList.indexOf('--region') + 1], 'us-east-1');
    assert.ok(argumentsList.includes('--no-cli-pager'));
    return { status: 0, stdout: '{}' };
  });
});

test('wrong AWS identity stops all further API calls', async () => {
  await withReport('preflight', async report => {
    let calls = 0;
    await preflight(config, catalog, report, () => { calls++; return { Account: 'other', Arn: 'root' }; });
    assert.equal(calls, 1);
    assert.equal(report.finish(), 1);
  });
});

test('preflight continues independent checks after denied API and never reports readiness', async () => {
  await withReport('preflight', async report => {
    const calls = [];
    await preflight(config, catalog, report, (service, operation) => {
      calls.push(`${service} ${operation}`);
      if (service === 'sts') return { Account: config.account, Arn: `arn:aws:sts::${config.account}:assumed-role/tc-oficina-p4-demo-a-provision/test` };
      throw new Error('AccessDenied');
    });
    assert.ok(calls.includes('freetier get-account-plan-state'));
    assert.ok(calls.includes('eks describe-cluster-versions'));
    assert.ok(report.data.checks.some(check => check.reason === 'INDEPENDENT_RECOVERY_CANARY_REQUIRED'));
    assert.equal(report.finish(), 1);
  });
});

test('report sanitization excludes arbitrary exception text and writes artifacts on failure', async () => {
  await withReport('up', async report => {
    const secret = 'password=synthetic-secret JWT=synthetic-token -----BEGIN PRIVATE KEY-----';
    await report.check('terraform-plan', 'infra', () => { throw new Error(secret); });
    assert.equal(report.finish(), 1);
    for (const name of ['report.json', 'summary.md']) {
      const output = fs.readFileSync(path.join(report.directory, name), 'utf8');
      assert.ok(!output.includes('synthetic'));
      assert.ok(!output.includes('PRIVATE KEY'));
    }
    for (const field of ['phase', 'resource', 'status', 'duration_ms', 'reason', 'next_action']) assert.ok(field in report.data.checks[0]);
  });
});

test('Terraform failure retains original exit result and prevents later validation commands', async () => {
  await withReport('offline', async report => {
    const calls = [];
    const success = await terraformValidation('phase4/stacks/infra', report, (executable, argumentsList) => {
      calls.push(argumentsList);
      return argumentsList.includes('init') ? { status: 1, stderr: 'Backend failed password=private' } : { status: 0 };
    });
    assert.equal(success, false);
    assert.equal(calls.length, 2);
    assert.equal(report.data.checks.at(-1).reason, 'Backend');
    assert.ok(calls[1].includes('-backend=false'));
    assert.equal(report.finish(), 1);
  });
});

test('Kubernetes collection errors cannot replace an original Terraform error', async () => {
  await withReport('up', async report => {
    await report.check('terraform-apply', 'infra', () => { throw new Error('StateLock'); });
    await collectKubernetes('tc-oficina-p4-demo-a', report, () => ({ status: 1, stderr: 'Forbidden secret=private' }));
    assert.equal(report.data.checks[0].reason, 'StateLock');
    assert.equal(report.finish(), 1);
    assert.ok(fs.existsSync(path.join(report.directory, 'kubernetes.json')));
  });
});

test('Kubernetes projection excludes env, annotations, arbitrary messages and secrets', () => {
  const summary = summarizeKubernetes({ items: [{ kind: 'Pod', metadata: { name: 'auth', annotations: { secret: 'synthetic-value' } },
    spec: { containers: [{ env: [{ name: 'PASSWORD', value: 'synthetic-value' }] }] },
    status: { message: 'synthetic-value', containerStatuses: [{ name: 'auth', ready: false, restartCount: 3,
      state: { waiting: { reason: 'CrashLoopBackOff', message: 'synthetic-value' } } }] } }] });
  assert.ok(!JSON.stringify(summary).includes('synthetic-value'));
  assert.equal(summary[0].containers[0].reason, 'CrashLoopBackOff');
});

test('command timeouts fail and do not forward captured output', () => {
  const result = command('terraform', [], () => ({ status: null, error: new Error('timeout'), stderr: 'private' }));
  assert.equal(result.status, 1);
  assert.equal(result.reason, 'COMMAND_FAILED');
  assert.equal(errorCode('unrecognized secret string'), 'COMMAND_FAILED');
});

test('bounded failure log signatures exclude passwords and application payloads', () => {
  const result = logSignatures('FlywayException: password=synthetic-secret JWT=synthetic-token\nPSQLException Connection refused');
  assert.ok(result.signatures.includes('FlywayException'));
  assert.ok(result.signatures.includes('Connection refused'));
  assert.ok(!JSON.stringify(result).includes('synthetic'));
});

test('failed pods trigger bounded current and previous log collection without leaking event messages', async () => {
  await withReport('up', async report => {
    const calls = [];
    await collectKubernetes('tc-oficina-p4-demo-a', report, (executable, argumentsList) => {
      calls.push(argumentsList);
      if (argumentsList[0] === 'logs') return { status: 0, stdout: 'Connection refused password=synthetic-secret' };
      if (argumentsList[1] === 'events') return { status: 0, stdout: JSON.stringify({ items: [{ reason: 'FailedMount', message: 'synthetic-secret' }] }) };
      return { status: 0, stdout: JSON.stringify({ items: [{ kind: 'Pod', metadata: { name: 'billing' },
        status: { phase: 'Failed' } }] }) };
    });
    assert.ok(calls.some(argumentsList => argumentsList.includes('--previous')));
    assert.ok(calls.filter(argumentsList => argumentsList[0] === 'logs').every(argumentsList => argumentsList.includes('--limit-bytes=16384')));
    assert.ok(!fs.readFileSync(path.join(report.directory, 'kubernetes.json'), 'utf8').includes('synthetic-secret'));
  });
});

test('Up and Destroy share a lock and never cancel the running operation', () => {
  for (const name of ['environment-up.yml', 'environment-destroy.yml', 'environment-bootstrap-identities.yml', 'environment-verify-recovery.yml']) {
    const workflow = fs.readFileSync(path.join(root, '.github/workflows', name), 'utf8');
    assert.match(workflow, /group: phase4-demo-a-mutation/);
    assert.match(workflow, /cancel-in-progress: false/);
    assert.match(workflow, /workflow_dispatch:/);
    assert.doesNotMatch(workflow, /pull_request:|push:|schedule:/);
    assert.match(workflow, /if: \$\{\{ always\(\) \}\}/);
  }
});

test('legacy has no auth, Terraform, AWS or destructive commands', () => {
  const workflow = fs.readFileSync(path.join(root, '.github/workflows/ci-cd.yml'), 'utf8');
  assert.doesNotMatch(workflow, /terraform\s+(apply|destroy|plan)|id-token:|configure-aws|kubectl|helm/);
});

test('planning gate authorizes only infra planning without enabling apply', () => {
  const disabledCatalog = { ...catalog, enabled: false };
  const disabledConfiguration = { ...config, execution_enabled: false };
  const runtime = { approved: false, account: config.account, region: config.region, environment: 'demo-a',
    backend_bucket: catalog.backend_bucket, preflight_authorization: { account: config.account, region: config.region,
      scope: 'infra', valid_until: Date.now() / 1000 + 3600, reference: 'offline-backend-review', backend_permissions_reviewed: true } };
  const planning = { ...environment, EXECUTION_MODE: 'plan', PHASE: 'infra', PHASE4_PLAN_APPROVED: 'true' };
  operationalGate('up', 'UP demo-a', disabledConfiguration, disabledCatalog, planning, runtime);
  for (const override of [{ PHASE4_PLAN_APPROVED: 'false' }, { PHASE: 'platform' }, { PLAN_HASH: 'a'.repeat(64) }]) {
    assert.throws(() => operationalGate('up', 'UP demo-a', disabledConfiguration, disabledCatalog,
      { ...planning, ...override }, runtime));
  }
  assert.throws(() => operationalGate('up', 'UP demo-a', disabledConfiguration, disabledCatalog,
    { ...planning, EXECUTION_MODE: 'apply', PLAN_HASH: '' }, runtime), /APPROVED_PLAN_HASH_REQUIRED/);
  assert.equal(disabledCatalog.enabled, false);
  assert.equal(runtime.approved, false);
});

test('workflow contracts preserve reusable defaults and propagate explicit planning intent', () => {
  const preflight = fs.readFileSync(path.join(root, '.github/workflows/environment-preflight.yml'), 'utf8');
  assert.match(preflight, /workflow_dispatch:[\s\S]*?scope:[\s\S]*?default: infra[\s\S]*?- infra[\s\S]*?- full/);
  assert.match(preflight, /workflow_call:[\s\S]*?scope:[\s\S]*?default: full/);
  assert.match(preflight, /intent:[\s\S]*?default: apply/);
  assert.ok(preflight.includes("PREFLIGHT_INTENT: ${{ inputs.intent || 'inspect' }}"));
  const up = fs.readFileSync(path.join(root, '.github/workflows/environment-up.yml'), 'utf8');
  assert.match(up, /execution_mode:[\s\S]*?default: plan/);
  assert.ok(up.includes('intent: ${{ inputs.execution_mode }}'));
  assert.ok(up.includes("--execution-mode '${{ inputs.execution_mode }}'"));
  assert.ok(up.includes("steps.aws.outcome == 'success' && inputs.execution_mode == 'apply'"));
});

test('bootstrap requires approved boundaries and excludes secret-bearing input variables', () => {
  assert.throws(() => validateInputs({}), /INPUTS_NOT_APPROVED/);
  const inputs = { environment_id: 'demo-a', aws_account_id: config.account, aws_region: config.region,
    backend_bucket: 'approved-backend', installation_approved: true, oidc_claims_verified: true,
    subject_format: 'immutable', workload_boundary_documents: Object.fromEntries(
      ['eks-cluster', 'eks-nodes', 'ebs-csi', 'load-balancer-controller', 'auth-lambda'].map(name => [name, '{}'])) };
  validateInputs(inputs);
  assert.throws(() => validateInputs({ ...inputs, secret_payload: 'forbidden' }));
});

test('bootstrap plan review rejects non-IAM resources and deletions; hash binds revision and changes', () => {
  const document = { resource_changes: [{ address: 'aws_iam_role.executor', type: 'aws_iam_role', change: { actions: ['create'], after: { name: 'role' } } }] };
  const review = reviewPlan(document, 'a'.repeat(40));
  assert.match(review.sha256, /^[a-f0-9]{64}$/);
  assert.notEqual(review.sha256, reviewPlan(document, 'b'.repeat(40)).sha256);
  assert.ok(!JSON.stringify(review).includes('after'));
  assert.throws(() => reviewPlan({ resource_changes: [{ type: 'aws_eks_cluster', change: { actions: ['create'] } }] }));
  assert.throws(() => reviewPlan({ resource_changes: [{ type: 'aws_iam_role', change: { actions: ['delete', 'create'] } }] }));
});
