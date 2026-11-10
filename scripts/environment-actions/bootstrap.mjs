import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { Report, aws, context, root } from './control.mjs';
import { command } from './diagnostics.mjs';
import { TerraformCommandError } from './terraform-diagnostics.mjs';

const boundaries = ['eks-cluster', 'eks-nodes', 'ebs-csi', 'load-balancer-controller', 'auth-lambda'];
const variableNames = new Set(['environment_id', 'aws_account_id', 'aws_region', 'backend_bucket',
  'installation_approved', 'oidc_claims_verified', 'subject_format', 'workload_boundary_documents',
  'inventory', 'inventory_approved', 'destroy_confirmation', 'external_input_secrets', 'artifact_bucket_installation_approved',
  'recovery_installation_approved', 'recovery_package_path', 'recovery_alarm_topic_arn',
  'recovery_reconcile_interval_minutes', 'recovery_opening_approved', 'recovery_verification_approved']);

export function validateInputs(inputs) {
  if (Object.keys(inputs).some(key => !variableNames.has(key)) || inputs.environment_id !== 'demo-a'
    || inputs.aws_account_id !== '446517826146' || inputs.aws_region !== 'us-east-1'
    || !/^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$/.test(inputs.backend_bucket || '')
    || inputs.installation_approved !== true || inputs.oidc_claims_verified !== true
    || !['classic', 'immutable'].includes(inputs.subject_format)
    || Object.keys(inputs.workload_boundary_documents || {}).sort().join() !== [...boundaries].sort().join()) {
    throw new Error('BOOTSTRAP_INPUTS_NOT_APPROVED');
  }
  if (inputs.recovery_installation_approved === true && (inputs.recovery_package_path !== 'endpoint-recovery.zip'
    || !/^arn:aws:sns:us-east-1:446517826146:[A-Za-z0-9_-]+$/.test(inputs.recovery_alarm_topic_arn || ''))) {
    throw new Error('RECOVERY_PACKAGE_OR_ALARM_REJECTED');
  }
  if ((inputs.recovery_opening_approved || inputs.recovery_verification_approved) && inputs.recovery_installation_approved !== true) {
    throw new Error('RECOVERY_INSTALLATION_REQUIRED');
  }
}

export function reviewPlan(document, revision, artifactsApproved = false, recoveryApproved = false) {
  const permitted = new Set(['aws_iam_role', 'aws_iam_policy', 'aws_iam_role_policy', 'aws_iam_role_policy_attachment', 'terraform_data']);
  if (artifactsApproved) {
    for (const type of ['aws_s3_bucket', 'aws_s3_bucket_versioning', 'aws_s3_bucket_public_access_block', 'aws_s3_bucket_server_side_encryption_configuration']) permitted.add(type);
  }
  if (recoveryApproved) {
    for (const type of ['aws_dynamodb_table', 'aws_sqs_queue', 'aws_scheduler_schedule_group', 'aws_scheduler_schedule',
      'aws_lambda_function', 'aws_lambda_function_event_invoke_config', 'aws_cloudwatch_log_group', 'aws_cloudwatch_metric_alarm']) permitted.add(type);
  }
  const changes = (document.resource_changes || []).filter(change => change.mode !== 'data');
  if (document.errored || document.deferred_changes?.length || changes.some(change =>
    !permitted.has(change.type) || change.change.actions.includes('delete'))) throw new Error('BOOTSTRAP_PLAN_SCOPE_REJECTED');
  const material = { revision, changes: changes.map(change => ({ address: change.address, type: change.type, change: change.change })) };
  const sha256 = crypto.createHash('sha256').update(JSON.stringify(material)).digest('hex');
  return { revision, sha256, changes: changes.map(change => ({ address: change.address, actions: change.change.actions })) };
}

async function main() {
  const report = new Report('bootstrap');
  let planFile;
  try {
    const success = await report.check('bootstrap', 'approved identity configuration', () => {
      context(JSON.parse(fs.readFileSync(path.join(root, 'phase4/environments/actions-first.json'))));
      validateInputs(JSON.parse(fs.readFileSync(path.join(root, 'phase4/bootstrap/identities/approved-inputs.json'))));
    }, 'Follow docs/github-actions-first.md. Supply five reviewed boundaries and verify actual OIDC subjects.');
    if (!success) return;
    const inputsFile = path.join(root, 'phase4/bootstrap/identities/approved-inputs.json');
    process.env.TF_WORKSPACE = 'default';
    const inputs = JSON.parse(fs.readFileSync(inputsFile));
    const identityReady = await report.check('bootstrap', 'temporary non-root identity', () => {
      const identity = aws('sts', 'get-caller-identity');
      if (identity.Account !== inputs.aws_account_id || !/^arn:aws:sts::446517826146:assumed-role\/tc-oficina-p4-demo-a-bootstrap\//.test(identity.Arn)) throw new Error('BOOTSTRAP_IDENTITY_REJECTED');
    });
    if (!identityReady) return;
    if (!process.env.RUNNER_TEMP) throw new Error('RUNNER_TEMP_REQUIRED');
    planFile = path.join(process.env.RUNNER_TEMP, 'phase4-identities.tfplan');
    const directory = path.join(root, 'phase4/bootstrap/identities');
    const invoke = argumentsList => {
      const result = command('terraform', [`-chdir=${directory}`, ...argumentsList]);
      if (result.status !== 0) throw new TerraformCommandError(result);
      return result.stdout;
    };
    for (const argumentsList of [
      ['fmt', '-check'],
      ['init', '-input=false', '-lockfile=readonly', `-backend-config=bucket=${inputs.backend_bucket}`,
        '-backend-config=region=us-east-1', '-backend-config=key=phase4/bootstrap/identities/demo-a.tfstate',
        '-backend-config=workspace_key_prefix=phase4/bootstrap/identities/demo-a-workspaces',
        '-backend-config=use_lockfile=true', '-backend-config=encrypt=true'],
      ['validate', '-json'],
      ['plan', '-input=false', '-lock-timeout=60s', `-var-file=${inputsFile}`, `-out=${planFile}`],
    ]) {
      if (!await report.check(`terraform-${argumentsList[0]}`, 'phase4/bootstrap/identities', () => invoke(argumentsList))) return;
    }
    let review;
    if (!await report.check('plan-review', 'approved persistent bootstrap resources', () => {
      review = reviewPlan(JSON.parse(invoke(['show', '-json', planFile])), process.env.GITHUB_SHA,
        inputs.artifact_bucket_installation_approved === true, inputs.recovery_installation_approved === true);
      fs.writeFileSync(path.join(report.directory, 'identity-plan.json'), JSON.stringify(review, null, 2));
    })) return;
    const approved = process.env.APPROVED_PLAN_SHA256 || '';
    if (!approved) {
      await report.check('plan-only', 'no resources created', () => {});
      return;
    }
    if (!await report.check('approval', 'exact plan fingerprint', () => {
      if (!/^[a-f0-9]{64}$/.test(approved) || approved !== review.sha256) throw new Error('APPROVED_PLAN_HASH_MISMATCH');
    }, 'Review identity-plan.json at the same revision and rerun with its SHA256 under environment approval.')) return;
    await report.check('terraform-apply', 'persistent identities only', () => invoke(['apply', '-input=false', '-lock-timeout=60s', planFile]));
  } catch {
    await report.check('bootstrap', 'configuration or executor', () => { throw new Error('BOOTSTRAP_EXECUTION_FAILED'); });
  } finally {
    if (planFile && fs.existsSync(planFile)) fs.unlinkSync(planFile);
    const status = report.finish();
    process.exitCode = report.data.checks.find(check => check.exit_code)?.exit_code || status;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
