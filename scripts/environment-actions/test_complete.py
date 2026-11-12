import base64
import copy
import json
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import complete
import pipeline
from aws_e2e import helpers, validate_fixture
from finalize import finalize
from runtime import Failure, Report, canonical, digest


class CompleteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.environment = patch.dict(os.environ, dict(PLAN_ARTIFACT_KEY=base64.b64encode(b'k' * 32).decode(),
            GITHUB_SHA='a' * 40, GITHUB_RUN_ID='123', E2E_FIXTURE_SECRET_ARN='fixture'), clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.config, self.catalog = pipeline.configuration()
        self.report = Report('plan', self.temporary.name)

    def plan_parts(self):
        directory = Path(self.temporary.name) / 'parts'
        for role in ('bootstrap', 'provision', 'deploy'):
            target = directory / role
            target.mkdir(parents=True)
            part = dict(configuration_sha256=complete.configuration_hash(self.config, self.catalog))
            if role != 'deploy':
                component = dict(environment='demo-a', account=complete.ACCOUNT, region=complete.REGION,
                    stack='identities' if role == 'bootstrap' else 'infra', phase='iam-maintenance' if role == 'bootstrap' else 'infra',
                    revision='a' * 40, run_id='123', states=dict(infra=None, platform=None, edge=None), binary_sha256=digest(b'mock-binary'))
                encrypted = pipeline.seal(dict(binary=base64.b64encode(b'mock-binary').decode()), component)
                part.update(summary=dict(manifest=component, sha256=digest(canonical(component) + encrypted)),
                    encrypted=base64.b64encode(encrypted).decode())
            (target / 'part.json').write_bytes(canonical(part))
            (target / 'report.json').write_bytes(canonical(dict(revision='a' * 40, checks=[])))
        return directory

    def test_assembly_rejects_mixed_configuration_from_different_plan_attempts(self):
        directory = self.plan_parts()
        path = directory / 'bootstrap/part.json'
        part = json.loads(path.read_text())
        part['configuration_sha256'] = '0' * 64
        path.write_bytes(canonical(part))
        with self.assertRaisesRegex(Failure, 'PLAN_PART_CONTEXT_MISMATCH'):
            complete.assemble(self.config, self.catalog, self.report, directory)
        self.assertFalse((self.report.directory / 'bundle.json').exists())

    def test_assembly_never_publishes_an_approved_bundle_for_blocked_check(self):
        directory = self.plan_parts()
        (directory / 'deploy/report.json').write_bytes(canonical(dict(revision='a' * 40,
            checks=[dict(phase='external', resource='credential', status='BLOCKED', reason='MISSING', duration_ms=0)])))
        with self.assertRaisesRegex(Failure, 'COMPLETE_PLAN_BLOCKED'):
            complete.assemble(self.config, self.catalog, self.report, directory)
        self.assertFalse((self.report.directory / 'bundle.json').exists())

    def test_missing_prerequisites_are_blocked_and_runtime_is_deferred(self):
        complete.prerequisites(self.config, self.report)
        self.report.finish()
        self.assertEqual(self.report.data['result'], 'BLOCKED')
        self.assertTrue(any(check['status'] == 'DEFERRED' for check in self.report.data['checks']))
        self.assertFalse(any(check['status'] == 'PASS' for check in self.report.data['checks']))

    def test_consolidation_preserves_blocked_and_deferred(self):
        complete.record(self.report, 'BLOCKED', 'images', 'PIN_REQUIRED')
        complete.record(self.report, 'DEFERRED', 'health', 'DEPLOY_REQUIRED')
        directory = Path(self.temporary.name) / 'execution'
        directory.mkdir()
        (directory / 'report.json').write_bytes(canonical(self.report.data))
        report, summary = finalize(self.temporary.name, {}, 'success', 'plan', 'complete', 'a' * 40)
        self.assertEqual(report['result'], 'BLOCKED')
        self.assertIn('DEFERRED', summary)

    def test_unique_report_publishes_bundle_hash_not_last_component_hash(self):
        for name, report in (('a-complete', dict(checks=[], plan_sha256='a' * 64, complete_plan_sha256='a' * 64)),
                             ('z-infra', dict(checks=[], plan_sha256='b' * 64))):
            directory = Path(self.temporary.name) / name
            directory.mkdir()
            (directory / 'report.json').write_bytes(canonical(report))
        result, summary = finalize(self.temporary.name, {}, 'success', 'plan', 'complete', 'c' * 40)
        self.assertEqual(result['plan_sha256'], 'a' * 64)
        self.assertEqual(result['component_plan_hashes']['z-infra'], 'b' * 64)
        self.assertIn('Plan SHA256: `' + 'a' * 64 + '`', summary)

    def test_pending_iam_secret_reads_remain_deferred_in_report_and_part(self):
        from unittest.mock import Mock
        os.environ['EXECUTOR_ROLE'] = 'deploy'
        config = dict(self.config,
            mercado_pago_test_secret_arn=f'arn:aws:secretsmanager:{complete.REGION}:{complete.ACCOUNT}:secret:/oficina/phase4/demo-a/inputs/mercado-pago-ABC123',
            e2e_fixture_secret_arn=f'arn:aws:secretsmanager:{complete.REGION}:{complete.ACCOUNT}:secret:/oficina/phase4/demo-a/inputs/e2e-fixture-ABC123')
        driver = Mock()
        driver.api.side_effect = Failure('AccessDenied')
        with patch('complete.driver_for', return_value=driver):
            complete.plan_part(config, self.catalog, self.report, 'deploy')
        part = json.loads((self.report.directory / 'part.json').read_text())
        self.assertEqual(set(part['runtime_inputs'].values()), {'DEFERRED'})
        self.assertEqual({check['status'] for check in self.report.data['checks']}, {'DEFERRED'})

    def test_deferred_plan_rejects_deletion_foreign_resource_and_cost_expansion(self):
        manifest = dict(deferred_scope=complete.deferred_scope(), max_deferred_resources=200)
        change = dict(address='aws_iam_role.workload', type='aws_iam_role', change=dict(actions=['create']))
        complete.review_deferred(dict(resource_changes=[change]), 'platform', manifest)
        for actions in (['delete'], ['delete', 'create'], ['forget']):
            rejected = copy.deepcopy(change)
            rejected['change']['actions'] = actions
            with self.assertRaisesRegex(Failure, 'DEFERRED_DESTRUCTIVE'):
                complete.review_deferred(dict(resource_changes=[rejected]), 'platform', manifest)
        change['type'] = 'aws_instance'
        with self.assertRaisesRegex(Failure, 'DEFERRED_RESOURCE_OUTSIDE_SCOPE'):
            complete.review_deferred(dict(resource_changes=[change]), 'platform', manifest)
        manifest['deferred_scope']['platform'].append('aws_instance')
        with self.assertRaisesRegex(Failure, 'DEFERRED_COST_EXPANSION'):
            complete.review_deferred(dict(resource_changes=[change]), 'platform', manifest)

    def test_deferred_plan_rejects_incomplete_or_expanded_plan(self):
        manifest = dict(deferred_scope=complete.deferred_scope(), max_deferred_resources=0)
        for document in (dict(errored=True), dict(deferred_changes=[{}])):
            with self.assertRaisesRegex(Failure, 'PLAN_INCOMPLETE'):
                complete.review_deferred(document, 'edge', manifest)
        with self.assertRaisesRegex(Failure, 'DEFERRED_SCOPE_EXPANSION'):
            complete.review_deferred(dict(resource_changes=[dict(mode='managed')]), 'edge', manifest)

    def test_deferred_lambda_cost_increase_and_unbounded_logs_are_rejected(self):
        manifest = dict(deferred_scope=complete.deferred_scope(), max_deferred_resources=200)
        change = dict(address='aws_lambda_function.auth', type='aws_lambda_function', change=dict(actions=['create'],
            after=dict(memory_size=512, reserved_concurrent_executions=2)))
        complete.review_deferred(dict(resource_changes=[change]), 'edge', manifest)
        change['change']['after']['memory_size'] = 1024
        with self.assertRaisesRegex(Failure, 'DEFERRED_LAMBDA_COST'):
            complete.review_deferred(dict(resource_changes=[change]), 'edge', manifest)
        change = dict(address='aws_cloudwatch_log_group.auth', type='aws_cloudwatch_log_group',
            change=dict(actions=['create'], after=dict(retention_in_days=0)))
        with self.assertRaisesRegex(Failure, 'DEFERRED_LOG_RETENTION'):
            complete.review_deferred(dict(resource_changes=[change]), 'edge', manifest)

    def test_bundle_binds_plan_run_revision_configuration_secret_and_hash(self):
        manifest = dict(operation='complete-deploy', run_id='42', revision='a' * 40,
            account=complete.ACCOUNT, region=complete.REGION, environment='demo-a',
            config_sha256=complete.configuration_hash(self.config, self.catalog), blocks=complete.BLOCKS,
            prerequisite_secret='fixture')
        encrypted = pipeline.seal(dict(bootstrap='saved-plan'), manifest)
        sha256 = digest(canonical(manifest) + encrypted)
        bundle = dict(manifest=manifest, encrypted=base64.b64encode(encrypted).decode(), sha256=sha256)
        path = Path(self.temporary.name) / 'bundle.json'
        path.write_bytes(canonical(bundle))
        run = dict(path='.github/workflows/plan.yml', event='workflow_dispatch', head_branch='homolog',
            head_sha='a' * 40, conclusion='success')
        with patch('pipeline.gh_json', return_value=run):
            self.assertEqual(complete.read_bundle(self.config, self.catalog, '42', sha256, path)[1], dict(bootstrap='saved-plan'))
            with self.assertRaisesRegex(Failure, 'HASH_MISMATCH'):
                complete.read_bundle(self.config, self.catalog, '42', '0' * 64, path)
            with self.assertRaisesRegex(Failure, 'PLAN_CONTEXT_MISMATCH'):
                complete.read_bundle(dict(self.config, changed=True), self.catalog, '42', sha256, path)
            with self.assertRaisesRegex(Failure, 'PLAN_CONTEXT_MISMATCH'):
                complete.read_bundle(self.config, self.catalog, '43', sha256, path)
        run['head_branch'] = 'main'
        with patch('pipeline.gh_json', return_value=run):
            with self.assertRaisesRegex(Failure, 'PROVENANCE_REJECTED'):
                complete.read_bundle(self.config, self.catalog, '42', sha256, path)

    def test_progress_is_bound_to_plan_deploy_run_and_revision(self):
        context = dict(plan_sha256='b' * 64, deploy_run='123', revision='a' * 40)
        progress = pipeline.seal(dict(next_block=1, states={}), context)
        self.assertEqual(pipeline.unseal(progress, context)['next_block'], 1)
        for changed in (dict(context, plan_sha256='c' * 64), dict(context, deploy_run='456'), dict(context, revision='d' * 40)):
            with self.assertRaisesRegex(Failure, 'AUTHENTICATION_FAILED'):
                pipeline.unseal(progress, changed)

    def test_delegation_never_promotes_application_or_destroy_environment_to_bootstrap(self):
        for environment, role in (('phase4-demo-a-deploy', 'bootstrap'), ('phase4-demo-a-deploy', 'provision'),
                ('phase4-demo-a-destroy', 'bootstrap'), ('phase4-demo-b-bootstrap', 'provision'),
                ('phase4-demo-a-bootstrap', 'destroy')):
            os.environ['PROTECTED_ENVIRONMENT'] = environment
            with patch('urllib.request.urlopen') as request:
                with self.assertRaisesRegex(Failure, 'DELEGATION_REJECTED'):
                    complete.assume(role)
                request.assert_not_called()

    def test_role_rotation_uses_fresh_oidc_and_rebuilds_the_sdk_session(self):
        from unittest.mock import Mock
        os.environ.update(PROTECTED_ENVIRONMENT='phase4-demo-a-bootstrap',
            ACTIONS_ID_TOKEN_REQUEST_URL='https://pipelines.actions.githubusercontent.com/token?audience=wrong',
            ACTIONS_ID_TOKEN_REQUEST_TOKEN='synthetic-runner-token')
        boto = Mock()
        boto.client.return_value.assume_role_with_web_identity.return_value = dict(Credentials=dict(
            AccessKeyId='synthetic-access', SecretAccessKey='synthetic-secret', SessionToken='synthetic-session'))
        propagation = RuntimeError('synthetic propagation delay')
        propagation.response = dict(Error=dict(Code='AccessDenied', Message='Trust propagation pending'))
        boto.client.return_value.assume_role_with_web_identity.side_effect = [propagation,
            boto.client.return_value.assume_role_with_web_identity.return_value]
        with patch.dict('sys.modules', {'boto3': boto, 'botocore': SimpleNamespace(UNSIGNED='unsigned'),
                'botocore.config': SimpleNamespace(Config=lambda **kwargs: kwargs)}), \
                patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"value":"synthetic-oidc"}')) as request, \
                patch('complete.time.sleep') as sleep:
            complete.assume('provision')
        sleep.assert_called_once_with(10)
        self.assertEqual(boto.client.return_value.assume_role_with_web_identity.call_count, 2)
        self.assertEqual(os.environ['EXECUTOR_ROLE'], 'provision')
        self.assertEqual(os.environ['AWS_SESSION_TOKEN'], 'synthetic-session')
        self.assertIn('audience=sts.amazonaws.com', request.call_args.args[0].full_url)
        self.assertNotIn('audience=wrong', request.call_args.args[0].full_url)
        self.assertEqual(boto.client.return_value.assume_role_with_web_identity.call_args.kwargs['RoleArn'],
            f'arn:aws:iam::{complete.ACCOUNT}:role/{complete.PREFIX}-provision')
        boto.setup_default_session.assert_called_once()

    def test_full_deploy_visits_every_internal_block_in_order_without_destroy(self):
        os.environ.update(PROTECTED_ENVIRONMENT='phase4-demo-a-bootstrap', PLAN_RUN='42', PLAN_HASH='b' * 64)
        calls = []
        with patch('pipeline.scope'), patch('pipeline.protection'), patch('complete.read_bundle'), \
                patch('complete.assume', side_effect=lambda role: calls.append(('role', role))), \
                patch('complete.execute_block', side_effect=lambda *args: calls.append(('block', args[3]))), \
                patch('complete.destroy_all') as destroyer:
            complete.deploy_all(self.config, self.catalog, self.report, self.temporary.name)
        self.assertEqual(calls, [entry for name, role, phases in complete.BLOCKS for entry in (('role', role), ('block', name))])
        destroyer.assert_not_called()

    def test_full_deploy_stops_at_failed_block_without_destroy(self):
        os.environ.update(PROTECTED_ENVIRONMENT='phase4-demo-a-bootstrap', PLAN_RUN='42', PLAN_HASH='b' * 64)
        with patch('pipeline.scope'), patch('pipeline.protection'), patch('complete.read_bundle'), patch('complete.assume'), \
                patch('complete.execute_block', side_effect=Failure('synthetic-failure')) as block, \
                patch('complete.destroy_all') as destroyer:
            with self.assertRaisesRegex(Failure, 'synthetic-failure'):
                complete.deploy_all(self.config, self.catalog, self.report, self.temporary.name)
        self.assertEqual(block.call_count, 1)
        destroyer.assert_not_called()

    def test_destroy_confirmation_precedes_any_oidc_request(self):
        os.environ.update(PROTECTED_ENVIRONMENT='phase4-demo-a-destroy', EXECUTOR_ROLE='destroy', CONFIRMATION='wrong',
            GITHUB_REPOSITORY=pipeline.REPOSITORY, GITHUB_REF='refs/heads/homolog', GITHUB_EVENT_NAME='workflow_dispatch')
        with patch('complete.assume') as assume:
            with self.assertRaisesRegex(Failure, 'CONFIRMATION_REJECTED'):
                complete.destroy_all(self.config, self.catalog, self.report)
            assume.assert_not_called()

    def test_e2e_fixture_rejects_mock_payments_and_foreign_environment(self):
        for fixture in (dict(environment='demo-b', payment_mode='mercado-pago-test'),
                        dict(environment='demo-a', payment_mode='mock')):
            with self.assertRaisesRegex(Failure, 'FIXTURE_SCOPE_REJECTED'):
                validate_fixture(fixture)

    def test_billing_helpers_do_not_execute_docker_or_fixture_globals(self):
        path = Path(self.temporary.name) / 'runner.py'
        path.write_text("raise RuntimeError('fixture should not run')\n" +
            '\n'.join('def ' + name + '(): return 1' for name in ('until', 'required', 'wait_api', 'login', 'check')))
        namespace = {}
        exec(helpers(path), namespace)
        self.assertEqual(namespace['login'](), 1)

    def test_current_homolog_mismatch_blocks_before_reading_old_ci(self):
        with patch('pipeline.gh_json', return_value=dict(object=dict(sha='f' * 40))):
            with self.assertRaisesRegex(Failure, 'HOMOLOG_CHANGED'):
                complete.sources(self.config, self.report)

    def test_complete_plan_part_never_calls_deploy_or_apply(self):
        os.environ['EXECUTOR_ROLE'] = 'provision'
        from unittest.mock import Mock
        driver = Mock()
        driver.run.return_value = json.dumps(dict(terraform_version='1.15.8'))
        (self.report.directory / 'plan.json').write_bytes(canonical(dict(manifest={})))
        (self.report.directory / 'approved-plan.enc').write_bytes(b'encrypted-plan-only')
        with patch('complete.driver_for', return_value=driver), patch('complete.prerequisites'), patch('complete.inspect_images'), \
                patch('complete.sources'), patch('pipeline.plan') as planner, patch('pipeline.deploy') as deployer:
            complete.plan_part(self.config, self.catalog, self.report, 'provision')
        planner.assert_called_once_with(driver, 'infra')
        deployer.assert_not_called()
        driver.tf.assert_not_called()
        driver.save.assert_not_called()
        self.assertTrue((self.report.directory / 'part.json').exists())

    def test_pinned_image_readback_rejects_architecture_and_digest_mismatch(self):
        from unittest.mock import Mock
        driver = Mock()
        driver.config = dict(terraform_inputs=dict(platform=dict(images=dict(mongodb='mongo@sha256:' + 'a' * 64))))
        driver.report = self.report
        driver.run.side_effect = ['', json.dumps([dict(Architecture='arm64', Os='linux')])]
        with self.assertRaisesRegex(Failure, 'DATA_IMAGE_PLATFORM_REJECTED'):
            complete.inspect_images(driver)
        driver.run.side_effect = ['', json.dumps([dict(Architecture='amd64', Os='linux', RepoDigests=['mongo@sha256:' + 'b' * 64])])]
        with self.assertRaisesRegex(Failure, 'DATA_IMAGE_DIGEST_MISMATCH'):
            complete.inspect_images(driver)

    def test_a_successful_check_for_another_revision_cannot_validate_current_source(self):
        source = self.config['services']['auth']
        with patch('pipeline.gh_json', side_effect=[dict(object=dict(sha=source['revision'])),
                [dict(check_runs=[dict(id=1, name='verify', app=dict(slug='github-actions'), head_sha='0' * 40,
                    status='completed', conclusion='success')])]]):
            with self.assertRaisesRegex(Failure, 'CURRENT_REVISION_CI_REQUIRED'):
                complete.sources(self.config, self.report)


if __name__ == '__main__':
    unittest.main()
