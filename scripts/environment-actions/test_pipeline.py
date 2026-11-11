import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import pipeline
from finalize import finalize
from runtime import ACCOUNT, Driver, Failure, Report, canonical, cleanup_steps, digest


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.environment = dict(GITHUB_REPOSITORY=pipeline.REPOSITORY, GITHUB_REF='refs/heads/homolog',
                                GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_SHA='a' * 40,
                                GITHUB_RUN_ID='123', PLAN_ARTIFACT_KEY=base64.b64encode(b'k' * 32).decode())
        self.patch = patch.dict(os.environ, self.environment, clear=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.report = Report('plan', self.temporary.name)
        self.config, self.catalog = pipeline.configuration()
        self.driver = Driver(self.config, self.report, catalog=self.catalog)
        self.addCleanup(self.driver.temporary.cleanup)
        self.commands = []
        self.driver.init = lambda stack: self.commands.append(('init', stack))
        self.driver.variables = lambda stack, phase: dict(environment_id='demo-a')
        self.driver.save = lambda: self.commands.append(('save',))
        self.driver.snapshot = lambda stack: self.commands.append(('snapshot', stack))
        self.driver.api = lambda *args, **kwargs: (_ for _ in ()).throw(Failure('404'))

        def terraform(stack, arguments):
            self.commands.append((arguments[0], stack))
            if arguments[0] == 'plan':
                Path(next(value.split('=', 1)[1] for value in arguments if value.startswith('-out='))).write_bytes(b'reviewed-terraform-binary')
            if arguments[0] == 'show':
                return json.dumps(dict(resource_changes=[dict(address='aws_vpc.environment', change=dict(actions=['create']))]))
            if arguments[0] == 'apply':
                self.assertEqual(Path(arguments[-1]).read_bytes(), b'reviewed-terraform-binary')
            return ''

        self.driver.tf = terraform
        self.run = dict(path='.github/workflows/plan.yml', event='workflow_dispatch', head_branch='homolog',
                        head_sha='a' * 40, conclusion='success')

    def make_plan(self):
        pipeline.plan(self.driver, 'infra')
        return json.loads((Path(self.temporary.name) / 'plan.json').read_text())

    def test_plan_on_disabled_demo_has_no_writes_or_apply_and_encrypts_binary(self):
        summary = self.make_plan()
        self.assertFalse(self.catalog['enabled'])
        self.assertFalse(any(command[0] in ('apply', 'save', 'snapshot', 'destroy') for command in self.commands))
        self.assertEqual(self.report.data['terraform_result'], 'PLAN_ONLY')
        self.assertNotIn(b'reviewed-terraform-binary', (Path(self.temporary.name) / 'approved-plan.enc').read_bytes())
        self.assertNotIn('variables', summary)

    def test_deploy_applies_identical_binary_without_replanning(self):
        summary = self.make_plan()
        self.commands.clear()
        with patch('pipeline.gh_json', return_value=self.run):
            pipeline.deploy(self.driver, 'infra', '123', summary['sha256'], self.temporary.name)
        self.assertIn(('apply', 'infra'), self.commands)
        self.assertFalse(any(command[0] == 'plan' for command in self.commands))

    def test_hash_and_encrypted_artifact_tampering_block_apply(self):
        summary = self.make_plan()
        with patch('pipeline.gh_json', return_value=self.run):
            with self.assertRaisesRegex(Failure, 'APPROVED_PLAN_HASH_MISMATCH'):
                pipeline.deploy(self.driver, 'infra', '123', 'b' * 64, self.temporary.name)
            artifact = Path(self.temporary.name) / 'approved-plan.enc'
            artifact.write_bytes(artifact.read_bytes() + b'tampered')
            with self.assertRaisesRegex(Failure, 'APPROVED_PLAN_HASH_MISMATCH'):
                pipeline.deploy(self.driver, 'infra', '123', summary['sha256'], self.temporary.name)
        self.assertNotIn(('apply', 'infra'), self.commands)

    def test_manifest_cannot_be_forged_even_with_recomputed_public_hash(self):
        summary = self.make_plan()
        summary['manifest']['binary_sha256'] = 'b' * 64
        encrypted = (Path(self.temporary.name) / 'approved-plan.enc').read_bytes()
        summary['sha256'] = digest(canonical(summary['manifest']) + encrypted)
        (Path(self.temporary.name) / 'plan.json').write_bytes(canonical(summary))
        with patch('pipeline.gh_json', return_value=self.run):
            with self.assertRaisesRegex(Failure, 'PLAN_ARTIFACT_AUTHENTICATION_FAILED'):
                pipeline.deploy(self.driver, 'infra', '123', summary['sha256'], self.temporary.name)

    def test_changed_state_inventory_phase_commit_or_failed_plan_block_deploy(self):
        summary = self.make_plan()
        with patch('pipeline.gh_json', return_value=self.run):
            self.driver.api = lambda *args, **kwargs: dict(VersionId='changed', ETag='changed')
            with self.assertRaisesRegex(Failure, 'TERRAFORM_STATE_CHANGED'):
                pipeline.deploy(self.driver, 'infra', '123', summary['sha256'], self.temporary.name)
            self.driver.api = lambda *args, **kwargs: (_ for _ in ()).throw(Failure('404'))
            with self.assertRaisesRegex(Failure, 'PLAN_CONTEXT_MISMATCH'):
                pipeline.deploy(self.driver, 'platform', '123', summary['sha256'], self.temporary.name)
            self.driver.journal['receipts']['changed'] = dict(verified=True)
            with self.assertRaisesRegex(Failure, 'INVENTORY_OR_CONFIGURATION_CHANGED'):
                pipeline.deploy(self.driver, 'infra', '123', summary['sha256'], self.temporary.name)
        for change in (dict(conclusion='failure'), dict(head_sha='b' * 40), dict(path='.github/workflows/deploy.yml')):
            with patch('pipeline.gh_json', return_value=dict(self.run, **change)):
                with self.assertRaisesRegex(Failure, 'PLAN_RUN_PROVENANCE_REJECTED'):
                    pipeline.deploy(self.driver, 'infra', '123', summary['sha256'], self.temporary.name)
        self.assertNotIn(('apply', 'infra'), self.commands)

    def test_deploy_requires_its_own_environment_and_actual_reviewers(self):
        pipeline.scope('plan', 'infra', self.environment)
        with self.assertRaisesRegex(Failure, 'ENVIRONMENT_APPROVAL_REQUIRED'):
            pipeline.scope('deploy', 'infra', self.environment)
        authorized = dict(self.environment, PROTECTED_ENVIRONMENT='phase4-demo-a-provision')
        pipeline.scope('deploy', 'infra', authorized)
        with patch.dict(os.environ, authorized), patch('pipeline.gh_json', return_value=dict(protection_rules=[])):
            with self.assertRaisesRegex(Failure, 'ENVIRONMENT_REVIEWERS_REQUIRED'):
                pipeline.protection()

    def test_destroy_requires_exact_confirmation_even_for_disabled_environment(self):
        for confirmation in ('', 'DESTROY', 'DESTROY demo-b'):
            with self.assertRaisesRegex(Failure, 'CONFIRMATION_REJECTED'):
                pipeline.scope('destroy', 'infra', dict(self.environment, CONFIRMATION=confirmation))
        pipeline.scope('destroy', 'infra', dict(self.environment, CONFIRMATION='DESTROY demo-a',
                                               PROTECTED_ENVIRONMENT='phase4-demo-a-destroy'))

    def test_original_apply_failure_survives_checkpoint_failure(self):
        summary = self.make_plan()
        original = self.driver.tf

        def terraform(stack, arguments):
            if arguments[0] == 'apply':
                raise Failure('AccessDenied', 'eks:CreateCluster')
            return original(stack, arguments)

        self.driver.tf = terraform
        self.driver.snapshot = lambda stack: (_ for _ in ()).throw(Failure('CHECKPOINT_DENIED'))
        with patch('pipeline.gh_json', return_value=self.run):
            with self.assertRaisesRegex(Failure, '^AccessDenied$'):
                pipeline.deploy(self.driver, 'infra', '123', summary['sha256'], self.temporary.name)

    def test_state_keys_are_scoped_and_access_denied_never_means_empty(self):
        commands = []
        driver = Driver(self.config, self.report, catalog=self.catalog)
        self.addCleanup(driver.temporary.cleanup)
        driver.tf = lambda stack, arguments: commands.append(arguments)
        for stack in pipeline.STACKS:
            driver.init(stack)
        for stack, arguments in zip(pipeline.STACKS, commands):
            self.assertIn(f'-backend-config=key=phase4/environments/demo-a/{stack}.tfstate', arguments)
            self.assertNotIn('demo-b', ' '.join(arguments))
        self.driver.api = lambda *args, **kwargs: (_ for _ in ()).throw(Failure('AccessDenied'))
        with self.assertRaisesRegex(Failure, 'AccessDenied'):
            pipeline.state_versions(self.driver)

    def test_maintenance_uses_existing_bootstrap_state_and_role_only_when_selected(self):
        self.assertEqual(pipeline.role_for('infra'), 'provision')
        self.assertEqual(pipeline.role_for('release'), 'deploy')
        for phase in pipeline.MAINTENANCE_PHASES:
            self.assertEqual(pipeline.role_for(phase), 'bootstrap')
        driver = pipeline.MaintenanceDriver.__new__(pipeline.MaintenanceDriver)
        driver.bucket = self.config['backend_bucket']
        commands = []
        driver.tf = lambda stack, arguments: commands.append((stack, arguments))
        driver.init('identities')
        self.assertEqual(commands[0][0], 'identities')
        self.assertIn('-backend-config=key=phase4/bootstrap/identities/demo-a.tfstate', commands[0][1])
        self.assertNotIn('infra.tfstate', ' '.join(commands[0][1]))

    def test_readonly_state_snapshot_never_writes_the_inventory(self):
        self.driver.tf = lambda stack, arguments: json.dumps(dict(values=dict(root_module=dict(resources=[]))))
        self.driver.save = lambda: (_ for _ in ()).throw(Failure('UNEXPECTED_BACKEND_WRITE'))
        self.assertEqual(Driver.snapshot(self.driver, 'infra', persist=False), [])
        with self.assertRaisesRegex(Failure, 'UNEXPECTED_BACKEND_WRITE'):
            Driver.snapshot(self.driver, 'infra')

    def test_optional_maintenance_preserves_scoped_iam_and_installed_recovery(self):
        driver = pipeline.MaintenanceDriver(self.config, self.report, 'artifact-foundation', catalog=self.catalog)
        self.addCleanup(driver.temporary.cleanup)
        saved = dict(inventory=dict(ec2=[f'arn:aws:ec2:us-east-1:{ACCOUNT}:vpc/vpc-owned']),
                     inventory_approved=True, destroy_confirmation='retained-reviewed-permission-scope')
        settings = dict(account=ACCOUNT, environment_id='demo-a', reconcile_interval_minutes=5, enabled=True)
        resources = [dict(type='aws_s3_bucket', values=dict(bucket=pipeline.PREFIX + '-artifacts')),
                     dict(type='aws_lambda_function', values=dict(function_name=pipeline.PREFIX + '-endpoint-recovery',
                          environment=[dict(variables=dict(RECOVERY_CONFIG=json.dumps(settings)))])),
                     dict(type='aws_cloudwatch_metric_alarm', values=dict(alarm_name=pipeline.PREFIX + '-recovery-errors',
                          alarm_actions=[f'arn:aws:sns:us-east-1:{ACCOUNT}:independent-alarms']))]
        document = dict(values=dict(outputs=dict(maintenance_inventory=dict(value=saved)), root_module=dict(resources=resources)))
        driver.api = lambda service, operation, **arguments: (dict(Account=ACCOUNT, Arn=f'arn:aws:sts::{ACCOUNT}:assumed-role/{pipeline.PREFIX}-bootstrap/test')
                     if service == 'sts' else dict(VersionId='known', ETag='known'))
        driver.init = lambda stack: None
        driver.tf = lambda stack, arguments: json.dumps(document)
        driver.run = lambda *args, **kwargs: (_ for _ in ()).throw(Failure('OFFLINE_VALIDATION_SENTINEL'))
        with self.assertRaisesRegex(Failure, 'OFFLINE_VALIDATION_SENTINEL'):
            driver.load()
        self.assertEqual(driver.maintenance_inputs['inventory'], saved['inventory'])
        self.assertTrue(driver.maintenance_inputs['artifact_bucket_installation_approved'])
        self.assertTrue(driver.maintenance_inputs['recovery_opening_approved'])
        self.assertTrue(driver.maintenance_inputs['recovery_installation_approved'])

    def test_deploy_entrypoint_blocks_before_aws_without_its_environment(self):
        with patch.dict(os.environ, REPORT_DIR=self.temporary.name), patch('sys.argv', ['pipeline.py', 'deploy', '--plan-run', '123', '--hash', 'b' * 64]), patch.object(Driver, 'load') as load:
            self.assertEqual(pipeline.main(), 1)
            load.assert_not_called()
        report = json.loads((Path(self.temporary.name) / 'report.json').read_text())
        self.assertEqual(report['checks'][0]['reason'], 'ENVIRONMENT_APPROVAL_REQUIRED')

    def test_technical_inputs_fail_early_without_inventing_external_integrations(self):
        pipeline.technical_inputs(self.config, 'infra')
        for phase, code in (('release', 'SOURCE_REVISION_AND_CHECKS_REQUIRED'),
                            ('platform', 'PINNED_DATA_IMAGE_REQUIRED'), ('e2e', 'AWS_E2E_ADAPTER_REQUIRED')):
            with self.assertRaisesRegex(Failure, code):
                pipeline.technical_inputs(self.config, phase)

    def test_diagnostics_preserve_original_failure_and_cleanup_status(self):
        for name, code in (('execution', 'AccessDenied'), ('endpoint-close', 'INDEPENDENT_CLOSE_NOT_CONFIRMED')):
            path = Path(self.temporary.name) / name
            path.mkdir()
            (path / 'report.json').write_text(json.dumps(dict(checks=[dict(phase=name, resource='eks', status='FAIL', reason=code)])))
        report, summary = finalize(self.temporary.name, dict(aws=dict(outcome='failure')), 'failure', 'deploy', 'infra', 'a' * 40)
        self.assertEqual(report['result'], 'FAIL')
        self.assertEqual(report['primary_failure']['reason'], 'AccessDenied')
        self.assertIn('AccessDenied', summary)
        self.assertIn('INDEPENDENT_CLOSE_NOT_CONFIRMED', summary)
        self.assertIn('GITHUB_STEP_FAILED', summary)
        self.assertTrue((Path(self.temporary.name) / 'summary.md').is_file())

    def test_cleanup_attempts_all_resources_and_preserves_primary_failure(self):
        attempted = []

        def fail():
            attempted.append('first')
            raise Failure('CLEANUP_FAILED')

        actions = [('first', fail), ('second', lambda: attempted.append('second'))]
        with self.assertRaisesRegex(Failure, 'ORIGINAL_FAILURE'):
            try:
                raise Failure('ORIGINAL_FAILURE')
            except Failure as original:
                cleanup_steps(self.report, actions, original)
                raise
        self.assertEqual(attempted, ['first', 'second'])
        self.assertEqual(self.report.data['checks'][0]['reason'], 'CLEANUP_FAILED')
        with self.assertRaisesRegex(Failure, 'CLEANUP_FAILED'):
            cleanup_steps(self.report, actions)

    def test_pretty_validate_json_keeps_sanitized_original_cause(self):
        diagnostic = dict(severity='error', summary='Invalid configuration', detail='Provider contract failed password=synthetic-secret')
        self.driver.execute = lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, json.dumps(dict(diagnostics=[diagnostic]), indent=2).encode(), b'')
        with self.assertRaises(Failure) as caught:
            self.driver.run(['terraform', 'validate', '-json'])
        detail = caught.exception.detail['diagnostics'][0]
        self.assertEqual(detail['summary'], 'Invalid configuration')
        self.assertIn('Provider contract failed', detail['detail'])
        self.assertNotIn('synthetic-secret', detail['detail'])

    def test_plain_backend_error_retains_aws_operation_and_code_without_raw_payload(self):
        output = b'operation error S3: ListObjectsV2 api error AccessDenied password=synthetic-secret'
        self.driver.execute = lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, b'', output)
        with self.assertRaises(Failure) as caught:
            self.driver.run(['terraform', 'init'])
        detail = caught.exception.detail['diagnostics'][0]
        self.assertEqual(detail['provider_operation'], 'operation error S3: ListObjectsV2')
        self.assertEqual(detail['aws_code'], 'AccessDenied')
        self.assertNotIn('synthetic-secret', json.dumps(detail))

    def test_partial_infra_recovery_continues_without_kubernetes_but_never_with_platform_state(self):
        driver = pipeline.PipelineDriver(self.config, self.report, catalog=self.catalog)
        self.addCleanup(driver.temporary.cleanup)
        driver.cluster = lambda missing_ok=False: dict(createdAt='owned-cluster-generation')
        driver.owned_load_balancers = lambda: []
        driver.data_volumes = lambda: []
        driver.api = lambda *args, **kwargs: (_ for _ in ()).throw(Failure('404'))
        driver.receipt = lambda key, value: driver.journal['receipts'].update({key: value})
        with patch.object(Driver, 'destroy') as destroy:
            driver.destroy()
            destroy.assert_called_once()
            driver.journal['receipts'] = {}
            driver.api = lambda *args, **kwargs: dict(VersionId='exists', ETag='exists')
            with self.assertRaisesRegex(Failure, 'PRIVILEGED_TEARDOWN_REQUIRED'):
                driver.destroy()
            destroy.assert_called_once()


if __name__ == '__main__':
    unittest.main()
