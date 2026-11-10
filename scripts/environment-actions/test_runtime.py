import copy
import io
import json
import os
from pathlib import Path
import subprocess
import time
import tempfile
import unittest
from unittest.mock import patch

from runtime import ACCOUNT, PREFIX, Driver, Failure, Report, guard, safe_error
from live_release import readback_image
from live_secrets import cleanup_secrets


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.report = Report('test', self.temporary.name)
        self.config = dict(approved=True, account=ACCOUNT, region='us-east-1', environment='demo-a',
            backend_bucket='approved-backend', destroy_offline_verified=True, terraform_inputs={})
        self.driver = Driver(self.config, self.report, sleep=lambda duration: None)
        self.addCleanup(self.driver.temporary.cleanup)
        self.catalog = dict(enabled=True, aws_account_id=ACCOUNT, backend_bucket='approved-backend')
        self.environment = dict(GITHUB_REPOSITORY='AuroNet0/tech-challenge-oficina-k8s-infra',
            GITHUB_REF='refs/heads/homolog', GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_SHA='a' * 40,
            PHASE4_EXECUTION_APPROVED='true', CONFIRMATION='UP demo-a')

    def test_authorization_before_aws(self):
        guard('up', self.config, self.catalog, self.environment)
        for change in [dict(CONFIRMATION='UP demo-b'), dict(GITHUB_REF='refs/heads/main'), dict(PHASE4_EXECUTION_APPROVED='false')]:
            with self.assertRaises(Failure):
                guard('up', self.config, self.catalog, dict(self.environment, **change))
        with self.assertRaisesRegex(Failure, 'ENVIRONMENT_DISABLED'):
            guard('up', self.config, dict(self.catalog, enabled=False), self.environment)

    def test_destroy_accepts_disabled_catalog_for_recovery(self):
        guard('destroy', self.config, dict(self.catalog, enabled=False),
              dict(self.environment, CONFIRMATION='DESTROY demo-a'))

    def test_unapproved_configuration_cannot_pass_the_execution_gate(self):
        with self.assertRaisesRegex(Failure, 'BACKEND_NOT_APPROVED'):
            guard('up', dict(self.config, approved=False), self.catalog, self.environment)
        with self.assertRaisesRegex(Failure, 'DESTROY_VALIDATION_REQUIRED'):
            guard('up', dict(self.config, destroy_offline_verified=False), self.catalog, self.environment)

    def test_plan_only_does_not_apply_and_hash_binds_approved_changes(self):
        document = dict(resource_changes=[dict(address='aws_vpc.environment', change=dict(actions=['create']))])
        commands = []
        self.driver.init = lambda stack: None
        self.driver.variables = lambda stack, profile: dict(environment_id='demo-a')
        self.driver.tf = lambda stack, arguments: (commands.append(arguments) or (json.dumps(document) if arguments[0] == 'show' else ''))
        self.driver.save = lambda: None
        self.driver.snapshot = lambda stack: []
        with patch.dict(os.environ, GITHUB_SHA='a' * 40):
            self.assertEqual(self.driver.plan_apply('infra'), 'PLAN_ONLY')
            self.assertFalse(any(command[0] == 'apply' for command in commands))
            fingerprint = json.loads((Path(self.temporary.name) / 'plan.json').read_text())['sha256']
            with self.assertRaisesRegex(Failure, 'APPROVED_PLAN_HASH_MISMATCH'):
                self.driver.plan_apply('infra', 'b' * 64)
            self.assertEqual(self.driver.plan_apply('infra', fingerprint), 'APPLIED')
        self.assertEqual(sum(command[0] == 'apply' for command in commands), 1)

    def test_partial_apply_preserves_original_failure_if_snapshot_also_fails(self):
        self.driver.init = lambda stack: None
        self.driver.variables = lambda stack, profile: {}
        self.driver.save = lambda: None
        self.driver.snapshot = lambda stack: (_ for _ in ()).throw(Failure('SNAPSHOT_DENIED'))
        self.driver.tf = lambda stack, args: json.dumps(dict(resource_changes=[])) if args[0] == 'show' else ''
        with patch.dict(os.environ, GITHUB_SHA='a' * 40):
            self.driver.plan_apply('infra')
            fingerprint = json.loads((Path(self.temporary.name) / 'plan.json').read_text())['sha256']
            original = self.driver.tf
            self.driver.tf = lambda stack, args: (_ for _ in ()).throw(Failure('ACCESS_DENIED')) if args[0] == 'apply' else original(stack, args)
            with self.assertRaisesRegex(Failure, 'ACCESS_DENIED'):
                self.driver.plan_apply('infra', fingerprint)

    def test_destroy_without_cluster_still_cleans_stacks_and_artifacts(self):
        order = []
        self.driver.cluster = lambda missing_ok=False: None
        self.driver.destroy_stack = lambda stack: order.append(stack)
        self.driver.cleanup_external = lambda: order.append('artifacts')
        self.driver.residuals = lambda: order.append('residuals')
        self.driver.destroy()
        self.assertEqual(order, ['edge', 'platform', 'artifacts', 'infra', 'residuals'])

    def test_partial_destroy_can_rerun_and_never_continues_to_infra_after_failure(self):
        order = []
        self.driver.cluster = lambda missing_ok=False: None

        def destroy(stack):
            order.append(stack)
            if stack == 'platform' and order.count('platform') == 1:
                raise Failure('ACCESS_DENIED')

        self.driver.destroy_stack = destroy
        self.driver.cleanup_external = lambda: order.append('artifacts')
        self.driver.residuals = lambda: order.append('residuals')
        with self.assertRaises(Failure):
            self.driver.destroy()
        self.assertNotIn('infra', order)
        self.driver.destroy()
        self.assertEqual(order[-5:], ['edge', 'platform', 'artifacts', 'infra', 'residuals'])

    def test_missing_state_is_not_treated_as_empty_if_inventory_exists(self):
        self.driver.journal['resources']['infra'] = [dict(id='vpc-123')]
        self.driver.api = lambda *args, **kwargs: (_ for _ in ()).throw(Failure('404'))
        with self.assertRaisesRegex(Failure, 'STATE_MISSING_WITH_INVENTORY'):
            self.driver.destroy_stack('infra')

    def test_access_denied_state_does_not_mean_not_found(self):
        self.driver.api = lambda *args, **kwargs: (_ for _ in ()).throw(Failure('AccessDenied'))
        with self.assertRaisesRegex(Failure, 'AccessDenied'):
            self.driver.destroy_stack('infra')

    def test_destroy_uses_saved_inputs_even_without_outputs(self):
        commands = []
        self.driver.journal['stacks']['infra'] = dict(node_count=2)
        self.driver.api = lambda *args, **kwargs: {}
        self.driver.init = lambda stack: None
        self.driver.snapshot = lambda stack: [dict(id='vpc-123')]
        self.driver.tf = lambda stack, arguments: commands.append((stack, arguments)) or ''
        self.driver.destroy_stack('infra')
        self.assertEqual(commands[0][0], 'infra')
        self.assertEqual(commands[0][1][0], 'destroy')
        self.assertTrue(any(argument.startswith('-var-file=') for argument in commands[0][1]))

    def test_sensitive_stdout_and_provider_detail_are_not_published(self):
        diagnostic = dict(diagnostic=dict(address='aws_vpc.environment', detail='operation error EC2: CreateVpc AccessDenied password=synthetic-secret JWT=synthetic-token'))
        self.driver.execute = lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, json.dumps(diagnostic).encode(), b'private-key synthetic-secret')
        with self.assertRaises(Failure):
            with self.report.phase('terraform-apply', 'infra'):
                self.driver.run(['terraform', 'apply', '-json'])
        report = (Path(self.temporary.name) / 'report.json').read_text()
        self.assertNotIn('synthetic', report)
        self.assertIn('aws_vpc.environment', report)
        self.assertIn('EC2: CreateVpc', report)

    def test_empty_terraform_stdout_preserves_stderr_cause(self):
        self.driver.execute = lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, b'', b'AccessDenied password=synthetic-secret')
        with self.assertRaisesRegex(Failure, 'ACCESS_DENIED'):
            self.driver.run(['terraform', 'init'])

    def test_last_json_progress_line_does_not_remove_provider_diagnostics(self):
        output = json.dumps(dict(diagnostic=dict(address='aws_eks_cluster.environment', detail='AccessDenied operation error EKS: CreateCluster')))
        output += '\n' + json.dumps(dict(type='apply_errored'))
        self.driver.execute = lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, output.encode(), b'')
        with self.assertRaises(Failure) as caught:
            self.driver.run(['terraform', 'apply', '-json'])
        self.assertEqual(caught.exception.detail['diagnostics'][0]['resource'], 'aws_eks_cluster.environment')

    def test_endpoint_configuration_requires_independent_canary_before_invocation(self):
        self.driver.cluster = lambda: dict(status='ACTIVE')
        with self.assertRaisesRegex(Failure, 'BROKER_CONFIGURATION_REQUIRED'):
            with self.driver.kubernetes():
                self.fail('must not enter')

    def test_close_without_lease_verifies_private_endpoint(self):
        self.driver.cluster = lambda missing_ok=False: dict(resourcesVpcConfig=dict(endpointPublicAccess=False, endpointPrivateAccess=True))
        self.driver.close_endpoint()
        self.driver.cluster = lambda missing_ok=False: dict(resourcesVpcConfig=dict(endpointPublicAccess=True, endpointPrivateAccess=True))
        with self.assertRaisesRegex(Failure, 'PUBLIC_ENDPOINT_WITHOUT_LEASE'):
            self.driver.close_endpoint()

    def test_diagnostics_never_falls_back_to_an_unscoped_kubeconfig(self):
        self.driver.run = lambda *args, **kwargs: self.fail('Collector must not access another cluster')
        with patch.dict(os.environ, KUBECONFIG=''):
            with self.assertRaisesRegex(Failure, 'SCOPED_KUBECONFIG_UNAVAILABLE'):
                self.driver.diagnostics()

    def test_secret_cleanup_is_exact_and_refuses_other_environment(self):
        self.driver.journal['secrets'] = {'auth-postgres': f'arn:aws:secretsmanager:us-east-1:{ACCOUNT}:secret:/oficina/phase4/demo-b/auth-postgres-abcdef'}
        with self.assertRaisesRegex(Failure, 'SECRET_DESTROY_SCOPE_REJECTED'):
            cleanup_secrets(self.driver)

    def test_secret_cleanup_recognizes_already_missing_secret(self):
        self.driver.journal['secrets'] = {'auth-postgres': f'arn:aws:secretsmanager:us-east-1:{ACCOUNT}:secret:/oficina/phase4/demo-a/auth-postgres-abcdef'}
        self.driver.api = lambda *args, **kwargs: (_ for _ in ()).throw(Failure('ResourceNotFoundException'))
        cleanup_secrets(self.driver)

    def test_journal_rejects_foreign_scope(self):
        with patch.dict(os.environ, EXECUTOR_ROLE='destroy'):
            def api(service, operation, **kwargs):
                if service == 'sts':
                    return dict(Account=ACCOUNT, Arn=f'arn:aws:sts::{ACCOUNT}:assumed-role/{PREFIX}-destroy/test')
                if operation == 'get_bucket_versioning':
                    return dict(Status='Enabled')
                return dict(Body=io.BytesIO(json.dumps(dict(account='other')).encode()))
            self.driver.api = api
            with self.assertRaisesRegex(Failure, 'INVENTORY_SCOPE_REJECTED'):
                self.driver.load()

    def test_temporary_endpoint_closes_after_failure_and_keyboard_interrupt(self):
        for failure in [Failure('APPLICATION_FAILURE'), KeyboardInterrupt()]:
            network = dict(endpointPublicAccess=False, endpointPrivateAccess=True, publicAccessCidrs=['8.8.8.8/32'])
            self.driver.config['endpoint'] = dict(mode='broker', independent_canary_verified=True)
            self.driver.cluster = lambda missing_ok=False: dict(status='ACTIVE', resourcesVpcConfig=network)
            invocations = []

            def invoke(purpose, payload):
                invocations.append((purpose, payload))
                if 'prepare' in payload:
                    return dict(lease_id='a' * 32, expires_at=time.time() + 1800)
                if 'open' in payload:
                    network['endpointPublicAccess'] = True
                    return {}
                network['endpointPublicAccess'] = False
                return dict(state='CLOSED')

            self.driver.invoke = invoke
            self.driver.receipt = lambda name, value: self.driver.journal['receipts'].update({name: value})
            self.driver.run = lambda *args, **kwargs: ''
            self.driver.diagnostics = lambda: (_ for _ in ()).throw(Failure('DIAGNOSTIC_FAILURE'))
            with patch.dict(os.environ, GITHUB_RUN_ID='1234', GITHUB_SHA='a' * 40), patch('urllib.request.urlopen', return_value=io.BytesIO(b'8.8.8.8\n')):
                with self.assertRaises(type(failure)):
                    with self.driver.kubernetes():
                        raise failure
            self.assertFalse(network['endpointPublicAccess'])
            self.assertEqual(invocations[-1][0], 'recovery')

    def test_close_failure_is_reported_without_hiding_original_error(self):
        self.driver.config['endpoint'] = dict(mode='broker', independent_canary_verified=True)
        self.driver.cluster = lambda missing_ok=False: dict(status='ACTIVE', resourcesVpcConfig=dict(
            endpointPublicAccess=False, endpointPrivateAccess=True, publicAccessCidrs=['8.8.8.8/32']))
        self.driver.receipt = lambda name, value: self.driver.journal['receipts'].update({name: value})
        self.driver.invoke = lambda purpose, payload: dict(lease_id='b' * 32, expires_at=time.time() + 1800)
        self.driver.wait = lambda *args, **kwargs: None
        self.driver.run = lambda *args, **kwargs: ''
        self.driver.diagnostics = lambda: None
        self.driver.close_endpoint = lambda: (_ for _ in ()).throw(Failure('INDEPENDENT_CLOSE_NOT_CONFIRMED'))
        with patch.dict(os.environ, GITHUB_RUN_ID='1234', GITHUB_SHA='a' * 40), patch('urllib.request.urlopen', return_value=io.BytesIO(b'8.8.8.8\n')):
            with self.assertRaisesRegex(Failure, 'WORKLOAD_FAILED'):
                with self.driver.kubernetes():
                    raise Failure('WORKLOAD_FAILED')
        self.assertTrue(any(check['reason'] == 'INDEPENDENT_CLOSE_NOT_CONFIRMED' for check in self.report.data['checks']))

    def test_ordered_teardown_retains_controller_until_ingress_and_volumes_are_gone(self):
        calls = []
        namespaces = [dict(metadata=dict(name=PREFIX, labels={'oficina/environment': 'demo-a'}))]

        def kubectl(arguments, document=None):
            calls.append(arguments)
            if arguments[:2] == ['get', 'namespaces']:
                return json.dumps(dict(items=namespaces))
            return json.dumps(dict(items=[]))

        self.driver.kubectl = kubectl
        self.driver.save = lambda: None
        self.driver.owned_load_balancers = lambda: []
        self.driver.data_volumes = lambda: []
        self.driver.run = lambda *args, **kwargs: '[]'
        self.driver.cleanup_kubernetes()
        operations = [' '.join(arguments[:3]) for arguments in calls]
        self.assertLess(operations.index('delete ingress --all'), operations.index('delete deployment,statefulset,job --all'))
        self.assertLess(operations.index('delete deployment,statefulset,job --all'), operations.index('delete pvc --all'))
        self.assertLess(operations.index('delete pvc --all'), operations.index('delete namespace ' + PREFIX))


if __name__ == '__main__':
    unittest.main()
