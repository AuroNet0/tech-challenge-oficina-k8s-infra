import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { validateInputs, reviewPlan } from './bootstrap.mjs';

const directory = new URL('../../phase4/bootstrap/identities/', import.meta.url);
const inputs = JSON.parse(fs.readFileSync(new URL('approved-inputs.json', directory)));
const array = value => Array.isArray(value) ? value : [value];
const documents = Object.fromEntries(Object.entries(inputs.workload_boundary_documents)
  .map(([name, document]) => [name, JSON.parse(document)]));

test('first bootstrap inputs match the installed account, backend and classic Environment subjects', () => {
  assert.doesNotThrow(() => validateInputs(inputs));
  assert.equal(inputs.backend_bucket, 'tech-challenge-oficina-terraform-state-b1cfa326');
  assert.equal(inputs.subject_format, 'classic');
  assert.deepEqual(inputs.inventory, {});
  assert.equal(inputs.inventory_approved, false);
  assert.deepEqual(inputs.external_input_secrets, {});
  assert.equal(inputs.destroy_confirmation, '');
  for (const flag of ['artifact_bucket_installation_approved', 'recovery_installation_approved',
    'recovery_opening_approved', 'recovery_verification_approved']) assert.equal(inputs[flag], false);
  const catalog = JSON.parse(fs.readFileSync(new URL('../../phase4/environments/catalog.json', import.meta.url)));
  assert.equal(catalog.environments['demo-a'].enabled, false);
  assert.equal(catalog.environments['demo-b'].enabled, false);
});

test('all five serialized boundaries match the readable review documents and IAM limits', () => {
  assert.equal(Object.keys(documents).length, 5);
  for (const [name, document] of Object.entries(documents)) {
    assert.deepEqual(document, JSON.parse(fs.readFileSync(new URL(`boundaries/demo-a/${name}.json`, directory))));
    assert.ok(inputs.workload_boundary_documents[name].length <= 6144, name);
    assert.equal(document.Version, '2012-10-17');
    assert.ok(document.Statement.length > 0);
    for (const statement of document.Statement) {
      assert.equal(statement.NotAction, undefined);
      assert.equal(statement.NotResource, undefined);
      for (const action of array(statement.Action)) {
        assert.equal(/[?*]/.test(action), false, action);
        assert.equal(/^(iam|sts):/i.test(action), false, action);
      }
      for (const resource of array(statement.Resource)) {
        if (resource !== '*') assert.ok(resource.includes(':us-east-1:'), resource);
        assert.equal(resource.includes('demo-b'), false);
      }
      if (statement.Effect === 'Allow' && statement.Resource === '*') {
        assert.equal(statement.Condition.StringEquals['aws:RequestedRegion'], 'us-east-1');
        assert.ok(array(statement.Action).every(action => action.startsWith('ec2:Describe')
          || action.startsWith('elasticloadbalancing:Describe') || action === 'ec2:GetSecurityGroupsForVpc'
          || action === 'ecr:GetAuthorizationToken' || statement.Sid === 'LambdaVpcServiceEni'));
      }
    }
  }
});

test('boundaries separate storage, networking, private ALB and Auth runtime privileges', () => {
  const actions = name => documents[name].Statement.flatMap(statement => array(statement.Action));
  assert.equal(actions('eks-cluster').some(action => action.startsWith('elasticloadbalancing:')), false);
  assert.ok(actions('eks-nodes').includes('eks-auth:AssumeRoleForPodIdentity'));
  assert.ok(actions('ebs-csi').includes('ec2:DeleteVolume'));
  assert.equal(actions('ebs-csi').some(action => /CreateSnapshot|FastSnapshot|CopyVolumes|LockSnapshot/.test(action)), false);
  assert.equal(actions('load-balancer-controller').some(action => /waf|shield|AuthorizeSecurityGroup|CreateSecurityGroup/.test(action)), false);
  assert.equal(documents['load-balancer-controller'].Statement.find(statement =>
    statement.Sid === 'CreateInternalEnvironmentAlb').Condition.StringEquals['elasticloadbalancing:Scheme'], 'internal');
  const secrets = documents['auth-lambda'].Statement.find(statement => statement.Sid === 'SelectedAuthSecrets');
  assert.equal(secrets.Resource.length, 2);
  assert.ok(secrets.Resource.every(resource => /\/auth-(postgres|signing)-\?{6}$/.test(resource)));
  assert.equal(actions('auth-lambda').includes('secretsmanager:PutSecretValue'), false);
  assert.equal(documents['auth-lambda'].Statement.find(statement =>
    statement.Sid === 'DenyFunctionCodeEniMutation').Effect, 'Deny');
});

const revision = 'a'.repeat(40);
const simulatedPlan = { resource_changes: [{ address: 'aws_iam_role.executor["provision"]',
  type: 'aws_iam_role', mode: 'managed', change: { actions: ['create'], after: { name: 'tc-oficina-p4-demo-a-provision' } } }] };

function runExecutor(approvedHash, failure = '') {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bootstrap-plan-only-'));
  try {
    const preload = path.join(temporary, 'mock-executables.mjs');
    fs.writeFileSync(preload, `
import childProcess from 'node:child_process';
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
childProcess.spawnSync = (executable, argumentsList) => {
  fs.appendFileSync(process.env.MOCK_CALLS, JSON.stringify([executable, argumentsList]) + '\\n');
  if (executable === 'aws' && argumentsList[0] === 'sts' && argumentsList[1] === 'get-caller-identity') {
    return { status: 0, stdout: JSON.stringify({ Account: '446517826146', Arn: 'arn:aws:sts::446517826146:assumed-role/tc-oficina-p4-demo-a-bootstrap/offline-test' }), stderr: '' };
  }
  if (executable !== 'terraform') throw new Error('UNEXPECTED_EXECUTABLE');
  const phase = argumentsList[1];
  if (!['fmt', 'init', 'validate', 'plan', 'show', 'apply'].includes(phase)) throw new Error('UNEXPECTED_PHASE');
  if (phase === process.env.MOCK_FAILURE) return { status: 1, stdout: '', stderr: 'AccessDenied: simulated offline failure' };
  return { status: 0, stdout: phase === 'show' ? ${JSON.stringify(JSON.stringify(simulatedPlan))} : '{}', stderr: '' };
};
syncBuiltinESMExports();
`);
    const result = spawnSync(process.execPath, ['--import', pathToFileURL(preload).href, fileURLToPath(new URL('bootstrap.mjs', import.meta.url))], {
      encoding: 'utf8', env: { ...process.env, GITHUB_REPOSITORY: 'AuroNet0/tech-challenge-oficina-k8s-infra',
        GITHUB_REF: 'refs/heads/homolog', GITHUB_EVENT_NAME: 'workflow_dispatch', GITHUB_SHA: revision,
        APPROVED_PLAN_SHA256: approvedHash, RUNNER_TEMP: temporary, REPORT_DIR: temporary,
        GITHUB_STEP_SUMMARY: path.join(temporary, 'github-summary.md'), MOCK_CALLS: path.join(temporary, 'calls.jsonl'),
        MOCK_FAILURE: failure, AWS_EC2_METADATA_DISABLED: 'true', NODE_OPTIONS: '' }, timeout: 15000,
    });
    assert.equal(result.error, undefined);
    assert.ok(fs.existsSync(path.join(temporary, 'calls.jsonl')), result.stderr || result.stdout);
    const calls = fs.readFileSync(path.join(temporary, 'calls.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
    const report = JSON.parse(fs.readFileSync(path.join(temporary, 'report.json')));
    return { status: result.status, phases: calls.filter(([executable]) => executable === 'terraform').map(([, argumentsList]) => argumentsList[1]), report };
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}

test('actual bootstrap entrypoint with empty hash ends plan-only and never invokes apply', () => {
  const result = runExecutor('');
  assert.equal(result.status, 0);
  assert.deepEqual(result.phases, ['fmt', 'init', 'validate', 'plan', 'show']);
  assert.ok(result.report.checks.some(check => check.phase === 'plan-only' && check.status === 'PASS'));
  assert.equal(result.report.environment_ready, false);
});

test('actual bootstrap entrypoint rejects an unapproved hash without apply', () => {
  const result = runExecutor('b'.repeat(64));
  assert.equal(result.status, 1);
  assert.equal(result.phases.includes('apply'), false);
  assert.ok(result.report.checks.some(check => check.reason === 'APPROVED_PLAN_HASH_MISMATCH'));
});

test('actual bootstrap entrypoint retains failed plan diagnostics without apply', () => {
  const result = runExecutor('', 'plan');
  assert.equal(result.status, 1);
  assert.deepEqual(result.phases, ['fmt', 'init', 'validate', 'plan']);
  assert.ok(result.report.checks.some(check => check.phase === 'terraform-plan' && check.status === 'FAIL' && check.reason === 'AccessDenied'));
});

test('apply path requires the exact reviewed hash, with all external commands mocked', () => {
  const result = runExecutor(reviewPlan(simulatedPlan, revision).sha256);
  assert.equal(result.status, 0);
  assert.equal(result.phases.at(-1), 'apply');
});
