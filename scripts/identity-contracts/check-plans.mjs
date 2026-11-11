import assert from 'node:assert/strict';
import fs from 'node:fs';
import readline from 'node:readline';
import { allowed, subject } from './contracts.mjs';

async function plans(filename) {
    const found = new Map();
    for await (const line of readline.createInterface({ input: fs.createReadStream(filename, 'utf8') })) {
        const clean = line.replace(/^\uFEFF/, '').trim();
        if (!clean) continue;
        const event = JSON.parse(clean);
        if (event.type === 'test_plan') found.set(event['@testrun'], event.test_plan);
        if (event.type === 'test_summary') assert.equal(event.test_summary.failed, 0);
    }
    return found;
}
const bootstrap = await plans(process.argv[2]);
const platform = await plans(process.argv[3]);
const base = bootstrap.get('persistent_oidc_and_no_opening');
const documents = Object.fromEntries(Object.entries(base.output_changes.policy_documents.after).map(([name, value]) => [name, JSON.parse(value)]));
const account = base.output_changes.identity_contract.after.aws_account_id;
assert.equal(account, '446517826146');
const prefix = `arn:aws:iam::${account}:role/tc-oficina-p4-demo-a-`;
const role = base.resource_changes.find(change => change.address === 'aws_iam_role.executor["deploy"]').change.after;
const trust = JSON.parse(role.assume_role_policy).Statement[0];
assert.equal(trust.Condition.StringEquals['token.actions.githubusercontent.com:sub'], subject('demo-a', 'deploy'));
assert.equal(trust.Condition.StringEquals['token.actions.githubusercontent.com:aud'], 'sts.amazonaws.com');
for (const foreign of [subject('demo-b', 'deploy'), subject('demo-a', 'destroy'), subject('demo-a', 'deploy').replace('AuroNet0', 'Foreign'), 'repo:AuroNet0/tech-challenge-oficina-k8s-infra:ref:refs/heads/homolog']) {
    assert.notEqual(trust.Condition.StringEquals['token.actions.githubusercontent.com:sub'], foreign);
}
assert.equal(allowed(documents.provision, 'iam:PassRole', `${prefix}auth-lambda`, { 'iam:PassedToService': 'lambda.amazonaws.com' }), true);
assert.equal(allowed(documents.provision, 'iam:PassRole', `${prefix}auth-lambda`, { 'iam:PassedToService': 'ec2.amazonaws.com' }), false);
assert.equal(allowed(documents.provision, 'iam:PassRole', `${prefix}provision`, { 'iam:PassedToService': 'lambda.amazonaws.com' }), false);
const jar = 'arn:aws:s3:::tc-oficina-p4-demo-a-artifacts/auth/synthetic/auth.jar';
assert.equal(allowed(documents.deploy, 's3:PutObject', jar, { 's3:ResourceAccount': account }), true);
assert.equal(allowed(documents.deploy, 's3:PutObject', jar, { 's3:ResourceAccount': '123456789012' }), false);
assert.equal(allowed(documents.deploy, 's3:PutObject', jar, { 's3:ResourceAccount': '210987654321' }), false);
const cluster = `arn:aws:eks:us-east-1:${account}:cluster/tc-oficina-p4-demo-a`;
const closed = { 'eks:endpointPublicAccess': 'false', 'eks:endpointPrivateAccess': 'true' };
assert.equal(allowed(documents.recovery, 'eks:UpdateClusterConfig', cluster, closed), true);
assert.equal(allowed(documents.recovery, 'eks:UpdateClusterConfig', cluster, { ...closed, 'eks:endpointPublicAccess': 'true' }), false);
assert.equal(allowed(documents.recovery, 'eks:UpdateClusterConfig', cluster.replace('demo-a', 'demo-b'), closed), false);
assert.equal(allowed(documents.recovery, 'eks:DeleteCluster', cluster), false);
assert.equal(allowed(documents.deploy, 'eks:UpdateClusterConfig', cluster, closed), false);
assert.equal(allowed(documents.provision, 'eks:UpdateClusterConfig', cluster, {}), false);
assert.equal(allowed(documents.provision, 'eks:UpdateClusterConfig', cluster, { ...closed, 'eks:endpointPublicAccess': 'true' }), false);
const destroy = JSON.parse(bootstrap.get('exact_destroy_inventory').output_changes.policy_documents.after.destroy);
const secret = `arn:aws:secretsmanager:us-east-1:${account}:secret:/oficina/phase4/demo-a/auth-postgres-ABC123`;
assert.equal(allowed(destroy, 'secretsmanager:DeleteSecret', secret, { 'aws:ResourceTag/EnvironmentId': 'demo-a' }), true);
assert.equal(allowed(destroy, 'secretsmanager:DeleteSecret', secret.replace('ABC123', 'DEF456'), { 'aws:ResourceTag/EnvironmentId': 'demo-a' }), false);
assert.equal(allowed(destroy, 'secretsmanager:DeleteSecret', secret, { 'aws:ResourceTag/EnvironmentId': 'demo-b' }), false);
const rendering = platform.get('private_alb_pod_identity');
const alb = JSON.parse(rendering.output_changes.alb_identity_contract.after.policy_json);
const albArn = 'arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/tc-oficina-p4-demo-a-edge/aaaaaaaaaaaaaaaa';
const albContext = {
    'aws:RequestTag/elbv2.k8s.aws/cluster': 'tc-oficina-p4-demo-a', 'aws:RequestTag/EnvironmentId': 'demo-a',
    'elasticloadbalancing:Scheme': 'internal', 'elasticloadbalancing:Subnet': ['subnet-00000000000000001', 'subnet-00000000000000002'],
    'elasticloadbalancing:SecurityGroup': ['sg-00000000000000009']
};
assert.equal(allowed(alb, 'elasticloadbalancing:CreateLoadBalancer', albArn, albContext), true);
for (const change of [{ 'elasticloadbalancing:Scheme': 'internet-facing' }, { 'elasticloadbalancing:SecurityGroup': ['sg-foreign'] }, { 'elasticloadbalancing:Subnet': ['subnet-foreign'] }, { 'aws:RequestTag/EnvironmentId': 'demo-b' }]) {
    assert.equal(allowed(alb, 'elasticloadbalancing:CreateLoadBalancer', albArn, { ...albContext, ...change }), false);
}
const rbac = JSON.parse(rendering.output_changes.executor_rbac.after);
assert.equal(rbac.items.length, 6);
for (const item of rbac.items) {
    if (item.kind === 'Role') assert.ok(['tc-oficina-p4-demo-a', 'tc-oficina-p4-demo-a-observability'].includes(item.metadata.namespace));
    for (const rule of item.rules || []) {
        assert.ok(!rule.verbs.some(verb => ['*', 'bind', 'escalate', 'impersonate'].includes(verb)));
        assert.ok(!rule.resources.some(resource => ['*', 'roles', 'rolebindings', 'clusterroles', 'clusterrolebindings', 'pods/exec'].includes(resource)));
        if (item.kind === 'ClusterRole') assert.ok(rule.verbs.every(verb => ['get', 'list', 'watch'].includes(verb)));
        if (rule.resources.includes('secrets')) assert.ok(!rule.verbs.includes('list') && !rule.verbs.includes('watch'));
    }
}
for (const policy of [...Object.values(documents), alb]) {
    assert.ok(policy.Statement.every(statement => statement.Effect === 'Deny' || ![statement.Action].flat().includes('*')));
}
if (process.argv[4]) fs.writeFileSync(process.argv[4], JSON.stringify(rbac));
console.log('IAM/RBAC static checks passed: exact OIDC, PassRole, Secrets inventory, close-only recovery, private ALB and namespace boundaries. Not an AWS IAM simulator.');
