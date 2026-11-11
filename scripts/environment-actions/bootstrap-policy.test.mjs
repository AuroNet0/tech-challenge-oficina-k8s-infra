import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const directory = new URL('../../phase4/bootstrap/identities/', import.meta.url);
const policy = JSON.parse(fs.readFileSync(new URL('console/demo-a-bootstrap-install-policy.json', directory)));
const statements = Object.fromEntries(policy.Statement.map(statement => [statement.Sid, statement]));
const account = '446517826146';
const prefix = 'tc-oficina-p4-demo-a';
const bucket = 'arn:aws:s3:::tech-challenge-oficina-terraform-state-b1cfa326';
const key = 'phase4/bootstrap/identities/demo-a.tfstate';
const array = value => Array.isArray(value) ? value : [value];
const shards = { Provision: 7, Deploy: 3, Destroy: 3 };
const shardArns = purpose => Array.from({ length: shards[purpose] }, (_, index) =>
  `arn:aws:iam::${account}:policy/${prefix}-${purpose.toLowerCase()}-${index}`);
const boundaryArns = ['eks-cluster', 'eks-nodes', 'ebs-csi', 'load-balancer-controller', 'auth-lambda']
  .map(name => `arn:aws:iam::${account}:policy/${prefix}-${name}-boundary`);

test('bootstrap inline policy is complete JSON within the role aggregate size limit', () => {
  assert.equal(policy.Version, '2012-10-17');
  assert.equal(Object.keys(statements).length, policy.Statement.length);
  assert.ok(JSON.stringify(policy).length <= 10240);
  for (const statement of policy.Statement) {
    assert.equal(statement.Effect, 'Allow');
    assert.equal(statement.Principal, undefined);
    assert.equal(statement.NotAction, undefined);
    assert.equal(statement.NotResource, undefined);
    for (const value of [...array(statement.Action), ...array(statement.Resource)]) {
      assert.equal(/[?*]/.test(value), false, value);
    }
    assert.ok(array(statement.Resource).every(resource => resource.startsWith('arn:aws:')));
  }
});

test('IAM permissions cover only the four declared roles and eighteen declared policies', () => {
  assert.deepEqual(statements.ManageDeclaredRoles.Resource, ['provision', 'deploy', 'destroy', 'endpoint-recovery']
    .map(name => `arn:aws:iam::${account}:role/${prefix}-${name}`));
  assert.deepEqual(statements.ManageDeclaredPolicies.Resource,
    [...Object.keys(shards).flatMap(shardArns), ...boundaryArns]);
  assert.equal(statements.InstallInactiveRecoveryInlinePolicy.Resource,
    `arn:aws:iam::${account}:role/${prefix}-endpoint-recovery`);
  assert.equal(statements.InstallInactiveRecoveryInlinePolicy.Action, 'iam:PutRolePolicy');
  assert.equal(statements.ReadExistingGithubProvider.Resource,
    `arn:aws:iam::${account}:oidc-provider/token.actions.githubusercontent.com`);
  const actions = policy.Statement.flatMap(statement => array(statement.Action));
  for (const forbidden of ['iam:PassRole', 'iam:DeleteRole', 'iam:DeletePolicy', 'iam:DetachRolePolicy',
    'iam:DeleteRolePolicy', 'iam:PutRolePermissionsBoundary', 'iam:DeleteRolePermissionsBoundary',
    'iam:CreateOpenIDConnectProvider', 'iam:CreateAccessKey', 'sts:AssumeRole']) {
    assert.equal(actions.includes(forbidden), false, forbidden);
  }
  assert.ok(!JSON.stringify(policy).includes(`role/${prefix}-bootstrap`));
  assert.ok(!JSON.stringify(policy).includes('demo-b'));
});

test('attachments cannot cross executor roles or attach arbitrary policies', () => {
  for (const purpose of Object.keys(shards)) {
    const statement = statements[`Attach${purpose}ShardsOnly`];
    assert.equal(statement.Action, 'iam:AttachRolePolicy');
    assert.equal(statement.Resource, `arn:aws:iam::${account}:role/${prefix}-${purpose.toLowerCase()}`);
    assert.deepEqual(statement.Condition.ArnEquals['iam:PolicyARN'], shardArns(purpose));
  }
});

test('backend object access is restricted to the state and lock, with no state deletion', () => {
  const s3Statements = policy.Statement.filter(statement => array(statement.Action).some(action => action.startsWith('s3:')));
  assert.equal(s3Statements.length, 4);
  for (const statement of s3Statements) {
    assert.equal(statement.Condition.StringEquals['s3:ResourceAccount'], account);
  }
  assert.deepEqual(statements.ReadWriteOnlyIdentityState.Action, ['s3:GetObject', 's3:PutObject']);
  assert.equal(statements.ReadWriteOnlyIdentityState.Resource, `${bucket}/${key}`);
  assert.deepEqual(statements.ManageOnlyIdentityLock.Action, ['s3:GetObject', 's3:PutObject', 's3:DeleteObject']);
  assert.equal(statements.ManageOnlyIdentityLock.Resource, `${bucket}/${key}.tflock`);
  assert.equal(statements.BackendBucketRegion.Resource, bucket);
  assert.equal(statements.BackendBucketRegion.Action, 's3:GetBucketLocation');
  assert.equal(statements.BackendRestrictedListing.Resource, bucket);
  assert.equal(statements.BackendRestrictedListing.Action, 's3:ListBucket');
  assert.deepEqual(statements.BackendRestrictedListing.Condition.StringEquals['s3:prefix'],
    [key, `${key}.tflock`, 'phase4/bootstrap/identities/demo-a-workspaces/']);
});

test('no future service installation is authorized and current Actions contracts still match', () => {
  assert.ok(policy.Statement.flatMap(statement => array(statement.Action))
    .every(action => action.startsWith('iam:') || action.startsWith('s3:')));
  const workflow = fs.readFileSync(new URL('../../.github/actions/setup/action.yml', import.meta.url), 'utf8');
  const executor = fs.readFileSync(new URL('bootstrap.mjs', import.meta.url), 'utf8');
  assert.ok(!fs.existsSync(new URL('../../.github/workflows/environment-bootstrap-identities.yml', import.meta.url)));
  assert.ok(workflow.includes('terraform_version: 1.15.8'));
  assert.ok(executor.includes("process.env.TF_WORKSPACE = 'default'"));
  assert.ok(executor.includes(`-backend-config=key=${key}`));
  assert.ok(executor.includes('-backend-config=workspace_key_prefix=phase4/bootstrap/identities/demo-a-workspaces'));
  assert.ok(executor.includes('-backend-config=use_lockfile=true'));
  for (const [filename, name] of [['artifacts.tf', 'artifact_bucket_installation_approved'],
    ['recovery.tf', 'recovery_installation_approved']]) {
    const source = fs.readFileSync(new URL(filename, directory), 'utf8');
    assert.match(source, new RegExp(`variable "${name}" \\{\\s+type\\s+= bool\\s+default = false`));
  }
});
