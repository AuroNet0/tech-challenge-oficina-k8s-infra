import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { TerraformCommandError, formatTerraformDiagnostic } from './terraform-diagnostics.mjs';

export const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const prefix = 'tc-oficina-p4-demo-a';
const allowed = new Set([
  'sts get-caller-identity', 'freetier get-account-plan-state',
  's3api head-bucket', 's3api get-bucket-location', 's3api get-bucket-versioning',
  's3api get-bucket-encryption', 's3api get-public-access-block', 's3api list-objects-v2', 's3api get-object',
  'iam get-open-id-connect-provider', 'iam get-role',
  'eks describe-cluster-versions', 'eks describe-cluster', 'eks list-clusters', 'eks describe-nodegroup',
  'rds describe-db-engine-versions', 'rds describe-orderable-db-instance-options', 'rds describe-db-instances',
  'ec2 describe-availability-zones', 'ec2 describe-instance-types', 'ec2 describe-instance-type-offerings',
  'ec2 describe-instances', 'ec2 describe-vpcs', 'service-quotas get-service-quota',
  'ecr describe-repositories', 'resourcegroupstaggingapi get-resources',
  'autoscaling describe-auto-scaling-groups',
  'lambda invoke',
  'elbv2 describe-load-balancers', 'elbv2 describe-target-groups', 'elbv2 describe-listeners', 'elbv2 describe-tags',
]);

export function errorCode(output) {
  if (/invalid choice/i.test(String(output))) return 'AWS_CLI_OPERATION_UNAVAILABLE';
  if (/acquiring the state lock|state lock|ConditionalCheckFailed/i.test(String(output))) return 'StateLock';
  if (/must be logged in|provide credentials|Unauthorized\b/i.test(String(output))) return 'KUBERNETES_AUTH_REJECTED';
  const known = ['AccessDenied', 'UnauthorizedOperation', 'Forbidden', 'NoSuchBucket', 'NoSuchEntity',
    'ResourceNotFound', 'NoSuchKey', 'DBInstanceNotFound', 'RepositoryNotFound', 'LoadBalancerNotFound', 'InvalidParameter', 'Unsupported',
    'ExpiredToken', 'InvalidClientTokenId', 'StateLock', 'Backend', 'InsufficientInstanceCapacity',
    'CrashLoopBackOff', 'ImagePullBackOff', 'ProgressDeadlineExceeded'];
  return known.find(code => String(output).toLowerCase().includes(code.toLowerCase())) || 'COMMAND_FAILED';
}

export function aws(service, operation, parameters = [], execute = spawnSync) {
  if (!allowed.has(`${service} ${operation}`)) throw new Error('READ_ONLY_ALLOWLIST_REJECTED');
  if (service === 'lambda' && (parameters[parameters.indexOf('--payload') + 1] !== '{"probe":true}'
    || parameters[parameters.indexOf('--function-name') + 1] !== 'arn:aws:lambda:us-east-1:446517826146:function:tc-oficina-p4-demo-a-endpoint-recovery')) {
    throw new Error('READ_ONLY_ALLOWLIST_REJECTED');
  }
  const result = execute('aws', [service, operation, ...parameters, '--region', 'us-east-1', '--output', 'json',
    '--no-cli-pager', '--cli-connect-timeout', '5', '--cli-read-timeout', '15'],
  { encoding: 'utf8', timeout: 45000, maxBuffer: 4 * 1024 * 1024, env: { ...process.env, AWS_MAX_ATTEMPTS: '2' } });
  if (result.status !== 0 || result.error) throw new Error(result.error?.code === 'ENOENT' ? 'AWS_CLI_UNAVAILABLE' : errorCode(result.stderr));
  try { return JSON.parse(result.stdout || '{}'); } catch { throw new Error('INVALID_JSON'); }
}

class AwsInspectionError extends Error {
  constructor(error, service, operation, permissions) {
    super(/^[A-Z][A-Z0-9_]{1,90}$/.test(error.message) ? error.message : errorCode(error.message));
    this.call = { service, operation };
    this.permissions = permissions;
  }
}

export class Report {
  constructor(operation, directory = process.env.REPORT_DIR || 'actions-report') {
    this.directory = directory;
    this.data = { schema_version: 1, operation, environment: 'demo-a',
      run_id: process.env.GITHUB_RUN_ID || 'offline', attempt: process.env.GITHUB_RUN_ATTEMPT || '1',
      revision: /^[a-f0-9]{40}$/.test(process.env.GITHUB_SHA || '') ? process.env.GITHUB_SHA : 'offline', checks: [], environment_ready: false };
  }
  async check(phase, resource, action, next = 'Review configuration and rerun the same workflow.') {
    const started = Date.now();
    let status = 'PASS';
    let reason = 'VERIFIED';
    let failure;
    try {
      const result = await action();
      if (result?.deferred) {
        status = 'DEFERRED';
        reason = /^[A-Z_]{3,100}$/.test(result.reason || '') ? result.reason : 'CONTROL_PLANE_ONLY_NO_KUBERNETES_ACCESS';
      }
    } catch (error) {
      status = 'FAIL';
      reason = /^[A-Z][A-Z0-9_]{1,90}$/.test(error.message) ? error.message : errorCode(error.message);
      if (error instanceof TerraformCommandError) {
        failure = { exit_code: error.exitCode, diagnostics: error.diagnostics };
        for (const diagnostic of error.diagnostics) {
          const message = formatTerraformDiagnostic(diagnostic).replace(/%/g, '%25').replace(/\r/g, '%0D').replace(/\n/g, '%0A');
          console.error(`::${diagnostic.severity}::${message}`);
        }
      }
      if (error instanceof AwsInspectionError) {
        failure = { aws_call: error.call, required_permissions: error.permissions };
        console.error(`AWS inspection failed: ${error.call.service} ${error.call.operation} (${reason})`);
      }
    }
    this.data.checks.push({ phase, resource, status, duration_ms: Date.now() - started, reason, next_action: status === 'PASS' ? 'None' : next, ...failure });
    this.write();
    return status === 'PASS';
  }
  write() {
    fs.mkdirSync(this.directory, { recursive: true });
    this.data.result = this.data.checks.some(check => check.status === 'FAIL') ? 'FAIL' : 'PASS';
    fs.writeFileSync(path.join(this.directory, 'report.json'), JSON.stringify(this.data, null, 2));
    const lines = [`# Environment ${this.data.operation}: ${this.data.result}`,
      `Run: ${this.data.run_id}, attempt: ${this.data.attempt}, revision: ${this.data.revision}`,
      ...(this.data.scope ? [`Scope: ${this.data.scope}; environment readiness is not attested.`] : []),
      '', '| Phase | Resource | Status | ms | Reason | Next action |', '|---|---|---|---:|---|---|',
      ...this.data.checks.map(check => `| ${check.phase} | ${check.resource} | ${check.status} | ${check.duration_ms} | ${check.reason} | ${check.next_action} |`)];
    for (const check of this.data.checks.filter(check => check.diagnostics)) {
      lines.push('', `## ${check.phase} (exit ${check.exit_code})`, '');
      for (const diagnostic of check.diagnostics) {
        const message = formatTerraformDiagnostic(diagnostic).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        lines.push(`<pre>${message}</pre>`, '');
      }
    }
    for (const check of this.data.checks.filter(check => check.aws_call)) {
      lines.push('', `AWS call: ${check.aws_call.service} ${check.aws_call.operation} (${check.reason})`);
      for (const permission of check.required_permissions) lines.push(`Required: ${permission.action} on ${permission.resource}`);
    }
    if (this.data.availability) {
      const evidence = this.data.availability;
      lines.push('', '## Availability evidence', '',
        `Region: ${evidence.region}; instance type: ${evidence.instance_type}`, '',
        '| Available AZ | Zone ID | Instance type offered |', '|---|---|---|',
        ...evidence.available_zones.map(zone => `| ${zone.name} | ${zone.id} | ${evidence.offered_zones.includes(zone.name) ? 'Yes' : 'No'} |`),
        '', `Candidates for operator review: ${evidence.candidate_zones.join(', ') || 'None'}. No zones selected automatically.`);
    }
    fs.writeFileSync(path.join(this.directory, 'summary.md'), lines.join('\n') + '\n');
  }
  finish() {
    this.write();
    if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY,
      fs.readFileSync(path.join(this.directory, 'summary.md')));
    return this.data.result === 'PASS' ? 0 : 1;
  }
}

function verify(condition, reason) { if (!condition) throw new Error(reason); }

export function availabilityEvidence(config, call) {
  verify(config.region === 'us-east-1' && config.instance_type === 'm7i-flex.large', 'NODE_CONFIGURATION_REJECTED');
  const zones = call('ec2', 'describe-availability-zones', ['--filters', 'Name=zone-type,Values=availability-zone']).AvailabilityZones;
  const offerings = call('ec2', 'describe-instance-type-offerings', ['--location-type', 'availability-zone', '--filters', `Name=instance-type,Values=${config.instance_type}`]).InstanceTypeOfferings;
  const zoneName = name => typeof name === 'string' && /^us-east-1[a-z]$/.test(name);
  const available = zones.filter(zone => zone.State === 'available' && zone.ZoneType === 'availability-zone'
    && ['opt-in-not-required', 'opted-in'].includes(zone.OptInStatus) && zoneName(zone.ZoneName))
    .map(zone => ({ name: zone.ZoneName, id: /^use1-az[0-9]+$/.test(zone.ZoneId || '') ? zone.ZoneId : 'unreported' }))
    .sort((first, second) => first.name.localeCompare(second.name));
  const offered = [...new Set(offerings.filter(item => item.InstanceType === config.instance_type
    && item.LocationType === 'availability-zone' && zoneName(item.Location)).map(item => item.Location))].sort();
  return { region: 'us-east-1', instance_type: 'm7i-flex.large', available_zones: available, offered_zones: offered,
    candidate_zones: available.filter(zone => offered.includes(zone.name)).map(zone => zone.name) };
}

export function nodeChildArns(inventory, call) {
  const resources = Object.values(inventory.resources).flat();
  const vpc = resources.find(resource => resource.type === 'aws_vpc');
  if (!vpc || !resources.some(resource => resource.type === 'aws_eks_cluster')) return [];
  let group;
  try { group = call('eks', 'describe-nodegroup', ['--cluster-name', prefix, '--nodegroup-name', `${prefix}-nodes`]).nodegroup; }
  catch (error) { if (error.message === 'ResourceNotFound') return []; throw error; }
  verify(group.clusterName === prefix && group.tags.EnvironmentId === 'demo-a', 'NODEGROUP_OWNERSHIP_REJECTED');
  const names = group.resources.autoScalingGroups.map(item => item.name);
  if (!names.length) return [];
  const groups = call('autoscaling', 'describe-auto-scaling-groups', ['--auto-scaling-group-names', ...names]).AutoScalingGroups;
  const identifiers = groups.flatMap(item => item.Instances.map(instance => instance.InstanceId));
  if (!identifiers.length) return [];
  const instances = call('ec2', 'describe-instances', ['--instance-ids', ...identifiers]).Reservations.flatMap(item => item.Instances);
  const arns = [];
  for (const instance of instances) {
    verify(instance.VpcId === vpc.id && identifiers.includes(instance.InstanceId), 'NODE_VPC_MISMATCH');
    arns.push(`arn:aws:ec2:us-east-1:446517826146:instance/${instance.InstanceId}`);
    arns.push(...instance.BlockDeviceMappings.map(item => `arn:aws:ec2:us-east-1:446517826146:volume/${item.Ebs.VolumeId}`));
    arns.push(...instance.NetworkInterfaces.map(item => `arn:aws:ec2:us-east-1:446517826146:network-interface/${item.NetworkInterfaceId}`));
  }
  return arns;
}

export function albChildArns(inventory, call) {
  const resources = Object.values(inventory.resources).flat();
  const vpc = resources.find(resource => resource.type === 'aws_vpc');
  if (!vpc || !inventory.resources.edge?.length) return [];
  let balancers;
  try { balancers = call('elbv2', 'describe-load-balancers', ['--names', `${prefix}-edge`]).LoadBalancers; }
  catch (error) { if (error.message === 'LoadBalancerNotFound') return []; throw error; }
  const arns = [];
  for (const balancer of balancers) {
    verify(balancer.VpcId === vpc.id && balancer.LoadBalancerName === `${prefix}-edge`, 'ALB_VPC_MISMATCH');
    const tags = call('elbv2', 'describe-tags', ['--resource-arns', balancer.LoadBalancerArn]).TagDescriptions[0].Tags;
    verify(tags.some(tag => tag.Key === 'EnvironmentId' && tag.Value === 'demo-a'), 'ALB_OWNERSHIP_REJECTED');
    arns.push(balancer.LoadBalancerArn);
    const groups = call('elbv2', 'describe-target-groups', ['--load-balancer-arn', balancer.LoadBalancerArn]).TargetGroups;
    verify(groups.every(group => group.VpcId === vpc.id), 'ALB_TARGET_VPC_MISMATCH');
    arns.push(...groups.map(group => group.TargetGroupArn));
    arns.push(...call('elbv2', 'describe-listeners', ['--load-balancer-arn', balancer.LoadBalancerArn]).Listeners.map(listener => listener.ListenerArn));
  }
  return arns;
}

export function context(config, environment = process.env) {
  verify(config.environment === 'demo-a' && config.account === '446517826146' && config.region === 'us-east-1', 'SCOPE_REJECTED');
  verify(environment.GITHUB_REPOSITORY === config.repository && environment.GITHUB_REF === 'refs/heads/homolog', 'REPOSITORY_OR_BRANCH_REJECTED');
  verify(environment.GITHUB_EVENT_NAME === 'workflow_dispatch', 'MANUAL_DISPATCH_REQUIRED');
}

export function currentReview(review, account, region, scope) {
  return review?.account === account && review.region === region && review.scope === scope
    && Number.isFinite(review.valid_until)
    && review.valid_until > Date.now() / 1000 && review.valid_until < Date.now() / 1000 + 86400
    && typeof review.reference === 'string' && review.reference.trim().length > 0;
}

export function planningGate(runtime, catalog, scope, environment = process.env) {
  verify(scope === 'infra', 'PLAN_SCOPE_REJECTED');
  verify(environment.PHASE4_PLAN_APPROVED === 'true', 'PLAN_NOT_AUTHORIZED');
  verify(runtime.account === '446517826146' && runtime.region === 'us-east-1' && runtime.environment === 'demo-a'
    && typeof runtime.backend_bucket === 'string' && /^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$/.test(runtime.backend_bucket)
    && catalog.aws_region === runtime.region
    && runtime.backend_bucket === catalog.backend_bucket && catalog.aws_account_id === runtime.account
    && currentReview(runtime.preflight_authorization, runtime.account, runtime.region, scope)
    && runtime.preflight_authorization.backend_permissions_reviewed === true, 'PLANNING_BACKEND_NOT_REVIEWED');
}

export function operationalGate(operation, confirmation, config, catalog, environment = process.env, runtime = {}) {
  context(config, environment);
  verify(['up', 'destroy'].includes(operation), 'OPERATION_REJECTED');
  verify(confirmation === `${operation.toUpperCase()} demo-a`, 'CONFIRMATION_REJECTED');
  if (operation === 'up') {
    const mode = environment.EXECUTION_MODE || 'apply';
    verify(['plan', 'apply'].includes(mode), 'EXECUTION_MODE_REJECTED');
    if (mode === 'plan') {
      verify(environment.PHASE === 'infra' && !environment.PLAN_HASH, 'PLAN_SCOPE_OR_HASH_REJECTED');
      planningGate(runtime, catalog, 'infra', environment);
      return;
    }
    verify(/^[a-f0-9]{64}$/.test(environment.PLAN_HASH || '') || !['infra', 'platform', 'edge'].includes((environment.PHASE || '').split('-')[0]), 'APPROVED_PLAN_HASH_REQUIRED');
  }
  verify((operation === 'destroy' || catalog.enabled) && catalog.aws_account_id === config.account && Boolean(catalog.backend_bucket), 'CATALOG_DISABLED');
  verify((operation === 'destroy' || config.execution_enabled === true) && environment.PHASE4_EXECUTION_APPROVED === 'true', 'EXECUTION_DISABLED');
}

function tcp(host) {
  return new Promise((resolve, reject) => {
    const socket = net.connect({ host, port: 443 });
    socket.setTimeout(5000);
    socket.once('connect', () => { socket.destroy(); resolve(); });
    socket.once('error', () => { socket.destroy(); reject(new Error('PRIVATE_ENDPOINT_UNREACHABLE')); });
    socket.once('timeout', () => { socket.destroy(); reject(new Error('PRIVATE_ENDPOINT_TIMEOUT')); });
  });
}

export function verifyInfraInputs(config, runtime) {
  const inputs = runtime.terraform_inputs?.infra || {};
  const roles = Object.fromEntries(['provision', 'deploy', 'destroy'].map(purpose =>
    [purpose, `arn:aws:iam::${config.account}:role/${prefix}-${purpose}`]));
  verify(JSON.stringify(inputs.availability_zones) === JSON.stringify(config.availability_zones)
    && inputs.node_count === config.node_count && inputs.node_count === 2
    && inputs.kubernetes_version === config.eks_version && inputs.postgres_version === config.postgres_version
    && inputs.db_instance_class === config.rds_class && inputs.db_allocated_storage === 20
    && inputs.eks_admin_role_arn === roles.provision
    && Object.keys(inputs.automation_roles || {}).length === 3
    && Object.entries(roles).every(([purpose, arn]) => inputs.automation_roles?.[purpose] === arn)
    && Object.keys(inputs.workload_permissions_boundaries || {}).length === 2
    && ['eks-cluster', 'eks-nodes'].every(name => inputs.workload_permissions_boundaries?.[name]
      === `arn:aws:iam::${config.account}:policy/${prefix}-${name}-boundary`)
    && Array.isArray(inputs.eks_public_access_cidrs) && inputs.eks_public_access_cidrs.length === 0,
  'INFRA_INPUTS_NOT_REVIEWED');
}

export async function preflight(config, catalog, report, call = aws, connect = tcp, runtimeOverride) {
  const runtimeFile = path.join(root, 'phase4/environments/demo-a.runtime.json');
  const runtime = runtimeOverride ?? (fs.existsSync(runtimeFile) ? JSON.parse(fs.readFileSync(runtimeFile)) : {});
  const scope = process.env.PREFLIGHT_SCOPE || 'full';
  const intent = process.env.PREFLIGHT_INTENT || 'apply';
  if (!await report.check('preflight-scope', 'requested scope', () => verify(['infra', 'full'].includes(scope), 'PREFLIGHT_SCOPE_REJECTED'))) return;
  if (!await report.check('preflight-intent', 'requested intent', () => verify(['inspect', 'plan', 'apply'].includes(intent)
    && (intent !== 'plan' || scope === 'infra'), 'PREFLIGHT_INTENT_REJECTED'))) return;
  report.data.scope = scope;
  report.data.intent = intent;
  report.data.free_plan_documentation = {
    source: 'https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html',
    reviewed_on: '2026-11-10', services_listed: ['eks', 'ec2', 'rds', 'ecr', 's3', 'iam'],
    account_configuration_eligibility_confirmed: false,
  };
  const review = runtime.preflight_authorization || {};
  const reviewCurrent = () => runtime.approved === true && currentReview(review, config.account, config.region, scope);
  const identityValid = await report.check('identity', 'STS', () => {
    const identity = call('sts', 'get-caller-identity');
    verify(identity.Account === config.account && /^arn:aws:sts::446517826146:assumed-role\/tc-oficina-p4-demo-a-provision\//.test(identity.Arn), 'OIDC_PROVISION_ROLE_REQUIRED');
  }, 'Install the provision role using the separate bootstrap procedure; never use root keys.');
  if (!identityValid) return;
  await report.check('configuration', 'catalog metadata', () => verify(catalog.aws_account_id === config.account
    && catalog.aws_region === config.region
    && /^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(catalog.backend_bucket || ''), 'CATALOG_INCOMPLETE'));
  await report.check('execution-gate', 'catalog and execution authorization', () => {
    if (intent === 'inspect') return { deferred: true, reason: 'INSPECTION_DOES_NOT_AUTHORIZE_PLAN_OR_APPLY' };
    if (intent === 'plan') { planningGate(runtime, catalog, scope); return; }
    verify(catalog.enabled, 'CATALOG_DISABLED');
    verify(config.execution_enabled === true, 'EXECUTION_DISABLED');
    verify(process.env.PHASE4_EXECUTION_APPROVED === 'true', 'EXECUTION_DISABLED');
  });
  if (scope === 'infra') await report.check('infra-inputs', 'Terraform and preflight contracts', () => verifyInfraInputs(config, runtime));
  await report.check('account-plan', 'Free Tier', () => {
    const plan = call('freetier', 'get-account-plan-state');
    verify(plan.accountPlanType === 'FREE' && plan.accountPlanStatus === 'ACTIVE', 'ACCOUNT_PLAN_INCOMPATIBLE');
  }, 'Verify plan status in AWS Console; do not upgrade implicitly.');
  await report.check('service-entitlements', 'FREE account-specific operator review', () => {
    if (intent !== 'apply') return { deferred: true, reason: 'ACCOUNT_CONFIGURATION_ELIGIBILITY_REQUIRED_FOR_APPLY' };
    const services = scope === 'infra' ? ['eks', 'rds', 'ec2', 'ecr', 'secretsmanager', 'logs', 'iam']
      : ['eks', 'rds', 'ec2', 'ecr', 'elbv2', 'lambda', 'secretsmanager', 'logs', 'iam'];
    verify(reviewCurrent() && review.free_plan_creation_reviewed === true
      && services.every(service => review.free_services_reviewed?.includes(service)), 'SERVICE_ENTITLEMENTS_NOT_PROVEN');
  },
    'Operator must review FREE creation eligibility for the services/configurations in this scope, credits and expiry; record a current evidence reference. Get/List calls do not prove creation eligibility.');
  await report.check('backend', 'S3 persistent backend', () => {
    const bucket = catalog.backend_bucket;
    verify(typeof bucket === 'string' && /^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(bucket), 'BACKEND_NOT_CONFIGURED');
    const owner = ['--bucket', bucket, '--expected-bucket-owner', config.account];
    const location = call('s3api', 'get-bucket-location', owner).LocationConstraint;
    verify(location === null || location === 'us-east-1', 'BACKEND_REGION_MISMATCH');
    verify(call('s3api', 'get-bucket-versioning', owner).Status === 'Enabled', 'BACKEND_VERSIONING_REQUIRED');
    verify(call('s3api', 'get-bucket-encryption', owner).ServerSideEncryptionConfiguration.Rules.length > 0, 'BACKEND_ENCRYPTION_REQUIRED');
    const block = call('s3api', 'get-public-access-block', owner).PublicAccessBlockConfiguration;
    verify(['BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets'].every(key => block[key] === true), 'BACKEND_PUBLIC_ACCESS_BLOCK_REQUIRED');
    call('s3api', 'list-objects-v2', [...owner, '--prefix', 'phase4/environments/demo-a/', '--max-keys', '10']);
  }, 'Select a private, encrypted, versioned backend in us-east-1. No write probe is performed.');
  await report.check('backend-permissions', 'operator review of backend state and lock writes', () => {
    if (intent === 'inspect') return { deferred: true, reason: 'BACKEND_WRITE_REVIEW_REQUIRED_FOR_PLAN' };
    if (intent === 'plan') { planningGate(runtime, catalog, scope); return; }
    verify(reviewCurrent() && review.backend_permissions_reviewed === true
      && runtime.backend_bucket === catalog.backend_bucket, 'BACKEND_WRITE_PERMISSIONS_NOT_PROVEN');
  },
    'Review exact state and .tflock permissions; read-only checks cannot prove writes.');
  await report.check('oidc', 'GitHub provider', () => {
    const provider = call('iam', 'get-open-id-connect-provider', ['--open-id-connect-provider-arn', `arn:aws:iam::${config.account}:oidc-provider/token.actions.githubusercontent.com`]);
    verify(provider.ClientIDList.includes('sts.amazonaws.com') && provider.Url.replace('https://', '').replace(/\/$/, '') === 'token.actions.githubusercontent.com', 'OIDC_PROVIDER_INCOMPATIBLE');
  });
  for (const purpose of ['provision', 'deploy', 'destroy']) {
    await report.check('roles', `${prefix}-${purpose}`, () => {
      const role = call('iam', 'get-role', ['--role-name', `${prefix}-${purpose}`]).Role;
      verify(role.Arn === `arn:aws:iam::${config.account}:role/${prefix}-${purpose}`, 'ROLE_ARN_MISMATCH');
      verify(role.AssumeRolePolicyDocument.Statement.some(statement => statement.Effect === 'Allow'
        && statement.Action === 'sts:AssumeRoleWithWebIdentity'
        && statement.Principal?.Federated === `arn:aws:iam::${config.account}:oidc-provider/token.actions.githubusercontent.com`
        && statement.Condition?.StringEquals?.['token.actions.githubusercontent.com:aud'] === 'sts.amazonaws.com'
        && [`repo:${config.repository}:environment:phase4-demo-a-${purpose}`,
          `repo:AuroNet0@128399450/tech-challenge-oficina-k8s-infra@1355187946:environment:phase4-demo-a-${purpose}`]
          .includes(statement.Condition?.StringEquals?.['token.actions.githubusercontent.com:sub'])), 'ROLE_TRUST_INCOMPATIBLE');
    }, 'Review actual GitHub subject configuration, trust and restricted permissions. Existence does not prove effective permissions.');
  }
  await report.check('versions', 'EKS', () => {
    const versions = call('eks', 'describe-cluster-versions', ['--cluster-versions', config.eks_version]);
    verify(versions.clusterVersions.some(version => version.clusterVersion === config.eks_version
      && [version.status, version.versionStatus].some(status => String(status).toUpperCase() === 'STANDARD_SUPPORT')), 'EKS_STANDARD_VERSION_UNAVAILABLE');
  });
  await report.check('versions', 'RDS PostgreSQL', () => {
    verify(call('rds', 'describe-db-engine-versions', ['--engine', 'postgres', '--engine-version', config.postgres_version])
      .DBEngineVersions.some(version => version.Status === 'available'), 'RDS_VERSION_UNAVAILABLE');
    verify(call('rds', 'describe-orderable-db-instance-options', ['--engine', 'postgres', '--engine-version', config.postgres_version,
      '--db-instance-class', config.rds_class]).OrderableDBInstanceOptions.length > 0, 'RDS_CLASS_UNAVAILABLE');
  });
  let availability;
  await report.check('availability-discovery', 'account AZs and instance type offerings', () => {
    verify(config.instance_type === 'm7i-flex.large', 'NODE_CONFIGURATION_REJECTED');
    availability = availabilityEvidence(config, call);
    report.data.availability = availability;
  }, 'Review available AZ names/IDs and instance type offerings in report.json before selecting two distinct AZs.');
  await report.check('network', 'Availability Zones', () => {
    verify(config.availability_zones.length === 2 && new Set(config.availability_zones).size === 2, 'TWO_AZS_REQUIRED');
    verify(availability, 'AZ_DISCOVERY_REQUIRED');
    verify(config.availability_zones.every(name => availability.available_zones.some(zone => zone.name === name)), 'AZ_UNAVAILABLE');
  });
  await report.check('capacity', 'two m7i-flex.large nodes', () => {
    verify(config.node_count === 2 && config.instance_type === 'm7i-flex.large', 'NODE_CONFIGURATION_REJECTED');
    verify(config.availability_zones.length === 2 && new Set(config.availability_zones).size === 2, 'TWO_AZS_REQUIRED');
    verify(availability, 'AZ_DISCOVERY_REQUIRED');
    verify(config.availability_zones.every(name => availability.candidate_zones.includes(name)), 'NODE_AZ_OFFERING_UNAVAILABLE');
    const type = call('ec2', 'describe-instance-types', ['--instance-types', config.instance_type]).InstanceTypes[0];
    const quota = call('service-quotas', 'get-service-quota', ['--service-code', 'ec2', '--quota-code', 'L-1216C47A']).Quota.Value;
    const instances = call('ec2', 'describe-instances', ['--filters', 'Name=instance-state-name,Values=pending,running,stopping,stopped']).Reservations.flatMap(item => item.Instances);
    const usage = instances.filter(instance => /^[acdhimrtz]/.test(instance.InstanceType) && instance.InstanceLifecycle !== 'spot')
      .reduce((total, instance) => total + instance.CpuOptions.CoreCount * instance.CpuOptions.ThreadsPerCore, 0);
    verify(Number.isFinite(usage) && quota - usage >= 2 * type.VCpuInfo.DefaultVCpus, 'EC2_VCPU_QUOTA_INSUFFICIENT');
  }, 'Offerings and quotas cannot guarantee live EC2 capacity. Review usage and regional restrictions.');
  await report.check('inventory', 'previous resources and conflicts', () => {
    verify(typeof catalog.backend_bucket === 'string' && /^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(catalog.backend_bucket), 'BACKEND_NOT_CONFIGURED');
    const inspect = (service, operation, parameters = []) => {
      try { return call(service, operation, parameters); }
      catch (error) {
        const permissions = service === 's3api' && operation === 'get-object'
          ? [{ action: 's3:GetObject', resource: `arn:aws:s3:::${catalog.backend_bucket}/phase4/environments/demo-a/operations/inventory.json` }] : [];
        throw new AwsInspectionError(error, service, operation, permissions);
      }
    };
    let inventory = { resources: {} };
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'phase4-preflight-'));
    try {
      const destination = path.join(directory, 'inventory.json');
      inspect('s3api', 'get-object', ['--bucket', catalog.backend_bucket, '--key', 'phase4/environments/demo-a/operations/inventory.json', '--expected-bucket-owner', config.account, destination]);
      inventory = JSON.parse(fs.readFileSync(destination));
      verify(inventory.account === config.account && inventory.region === config.region && inventory.environment === 'demo-a', 'INVENTORY_SCOPE_REJECTED');
    } catch (error) {
      if (error.message !== 'NoSuchKey') throw error;
    } finally { fs.rmSync(directory, { recursive: true, force: true }); }
    const known = Object.values(inventory.resources).flat();
    const external = [...Object.values(inventory.secrets || {}), ...(inventory.external_arns || []), ...nodeChildArns(inventory, inspect), ...albChildArns(inventory, inspect)];
    const tagged = inspect('resourcegroupstaggingapi', 'get-resources', ['--tag-filters', 'Key=EnvironmentId,Values=demo-a']).ResourceTagMappingList;
    const clusters = inspect('eks', 'list-clusters').clusters;
    const databases = inspect('rds', 'describe-db-instances').DBInstances;
    const vpcs = inspect('ec2', 'describe-vpcs', ['--filters', 'Name=cidr-block,Values=10.42.0.0/16']).Vpcs;
    const repositories = inspect('ecr', 'describe-repositories').repositories.filter(item => item.repositoryName.startsWith(prefix));
    verify(tagged.filter(item => item.Tags.some(tag => tag.Key === 'Lifecycle' && tag.Value === 'ephemeral')).every(item => known.some(resource => resource.arn === item.ResourceARN) || external.includes(item.ResourceARN))
      && (!clusters.includes(prefix) || known.some(resource => resource.type === 'aws_eks_cluster' && resource.id === prefix))
      && databases.filter(item => item.DBInstanceIdentifier === `${prefix}-postgres`).every(item => known.some(resource => resource.arn === item.DBInstanceArn))
      && vpcs.every(item => known.some(resource => resource.id === item.VpcId))
      && repositories.every(item => known.some(resource => resource.arn === item.repositoryArn)), 'EXISTING_RESOURCES_REQUIRE_STATE_RECONCILIATION');
  }, 'Review the failed AWS call and required permissions against the configured backend; reconcile exact ARNs before any IAM or state change.');
  await report.check('connectivity', 'private Kubernetes endpoint', async () => {
    if (scope === 'infra') {
      if (intent === 'apply') verify(['broker', 'existing-private'].includes(config.runner_mode) && runtime.endpoint?.mode === config.runner_mode
          && runtime.endpoint.private_creation_reviewed === true && runtime.endpoint.teardown_reviewed === true
          && runtime.destroy_offline_verified === true
          && typeof runtime.endpoint.recovery_reference === 'string' && runtime.endpoint.recovery_reference.trim().length > 0,
        'EKS_RECOVERY_STRATEGY_NOT_REVIEWED');
      try {
        const cluster = call('eks', 'describe-cluster', ['--name', prefix]).cluster;
        verify(cluster.resourcesVpcConfig.endpointPrivateAccess && !cluster.resourcesVpcConfig.endpointPublicAccess, 'INFRA_REQUIRES_CLOSED_ENDPOINT');
      } catch (error) { if (error.message !== 'ResourceNotFound') throw error; }
      return { deferred: true };
    }
    if (config.runner_mode === 'broker') {
      verify(runtime.endpoint?.mode === 'broker' && runtime.endpoint?.independent_canary_verified === true, 'INDEPENDENT_RECOVERY_CANARY_REQUIRED');
      const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'phase4-recovery-probe-'));
      try {
        const destination = path.join(directory, 'probe.json');
        const response = call('lambda', 'invoke', ['--function-name', `arn:aws:lambda:us-east-1:446517826146:function:${prefix}-endpoint-recovery`,
          '--invocation-type', 'RequestResponse', '--cli-binary-format', 'raw-in-base64-out', '--payload', '{"probe":true}', destination]);
        verify(!response.FunctionError && JSON.parse(fs.readFileSync(destination)).verified === true, 'LIVE_RECOVERY_PROBE_FAILED');
      } finally { fs.rmSync(directory, { recursive: true, force: true }); }
      return;
    }
    verify(config.runner_mode === 'existing-private' && process.env.RUNNER_ENVIRONMENT === 'self-hosted', 'PRIVATE_EXECUTOR_NOT_CONFIGURED');
    let host = config.private_probe_host;
    try {
      const cluster = call('eks', 'describe-cluster', ['--name', prefix]).cluster;
      verify(!cluster.resourcesVpcConfig.endpointPublicAccess && cluster.resourcesVpcConfig.endpointPrivateAccess, 'EKS_ENDPOINT_NOT_PRIVATE');
      host = new URL(cluster.endpoint).hostname;
    } catch (error) { if (error.message !== 'ResourceNotFound') throw error; }
    verify(typeof host === 'string' && /^[a-zA-Z0-9.-]+$/.test(host), 'PRIVATE_PATH_PROBE_REQUIRED');
    await connect(host);
  }, scope === 'infra'
    ? 'Review private creation and recovery; Kubernetes access and the independent canary must be proven after cluster creation, before dependent phases or any public opening.'
    : 'Provide an existing private runner with routing, DNS and outbound GitHub access. A TCP probe is not Kubernetes authorization.');
  await report.check('artifacts', 'four reviewed service revisions', () => {
    if (scope === 'infra') return { deferred: true, reason: 'SERVICE_ARTIFACTS_REQUIRED_AFTER_INFRA' };
    for (const service of ['auth', 'os', 'execution', 'billing']) {
      const source = runtime.services?.[service];
      verify(source && /^AuroNet0\/tech-challenge-oficina-[a-z-]+$/.test(source.repository)
        && /^[0-9a-f]{40}$/.test(source.revision) && Array.isArray(source.required_checks) && source.required_checks.length > 0, 'APPROVED_SERVICE_REVISIONS_REQUIRED');
      const response = spawnSync('gh', ['api', '--paginate', '--slurp', `repos/${source.repository}/commits/${source.revision}/check-runs?per_page=100`], { encoding: 'utf8', timeout: 30000 });
      verify(response.status === 0, 'SOURCE_OR_CI_ACCESS_DENIED');
      const checks = JSON.parse(response.stdout).flatMap(page => page.check_runs);
      verify(source.required_checks.every(name => {
        const check = checks.filter(item => item.name === name).sort((first, second) => second.id - first.id)[0];
        return check && check.head_sha === source.revision && check.status === 'completed' && check.conclusion === 'success';
      }), 'APPROVED_SERVICE_CI_REQUIRED');
    }
  }, scope === 'infra'
    ? 'Service publication and external integrations are deferred until their dependent phases; no application readiness is attested by Infra.'
    : 'Pin clean revisions and verify source access, CI, CA, signing, ACM, Mercado Pago and New Relic inputs.');
}

async function main() {
  const operation = process.argv[2];
  const report = new Report(operation);
  try {
    const config = JSON.parse(fs.readFileSync(path.join(root, 'phase4/environments/actions-first.json')));
    const catalog = JSON.parse(fs.readFileSync(path.join(root, 'phase4/environments/catalog.json'))).environments['demo-a'];
    const valid = await report.check('gate', 'repository and manual dispatch', () => context(config));
    if (valid && operation === 'preflight') await preflight(config, catalog, report);
    else if (valid) {
      const runtime = JSON.parse(fs.readFileSync(path.join(root, 'phase4/environments/demo-a.runtime.json')));
      await report.check('gate', 'requested operation', () => operationalGate(operation, process.env.CONFIRMATION, config, catalog, process.env, runtime));
    }
  } catch { await report.check('configuration', 'input files', () => { throw new Error('CONFIGURATION_INVALID'); }); }
  process.exitCode = report.finish();
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
