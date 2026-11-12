import base64
import json
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from package_recovery import package_bytes
from pipeline import PipelineDriver
from runtime import ACCOUNT, PREFIX, REGION, Failure, Report, digest


class PrivateBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.report = Report('deploy', self.temporary.name)
        self.report.data['phase'] = 'foundation'
        self.driver = PipelineDriver(dict(backend_bucket='offline', endpoint=dict(mode='broker')), self.report)
        self.addCleanup(self.driver.temporary.cleanup)
        self.checksum = base64.b64encode(bytes.fromhex(digest(package_bytes()))).decode()

    def installation(self, service, operation, **arguments):
        if service == 'scheduler':
            return dict(State='ENABLED', Target=dict(Arn=f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{PREFIX}-endpoint-recovery'))
        purpose = arguments['FunctionName'].rsplit('-', 1)[-1]
        settings = dict(account=ACCOUNT, region=REGION, environment_id='demo-a',
                        cluster_arn=f'arn:aws:eks:{REGION}:{ACCOUNT}:cluster/{PREFIX}',
                        bootstrap_principal_arn=f'arn:aws:iam::{ACCOUNT}:role/{PREFIX}-provision',
                        code_sha256=self.checksum, enabled=True, recovery_verified=True)
        return dict(State='Active', LastUpdateStatus='Successful', CodeSha256=self.checksum,
                    Role=f'arn:aws:iam::{ACCOUNT}:role/{PREFIX}-endpoint-{purpose}',
                    Environment=dict(Variables=dict(RECOVERY_CONFIG=json.dumps(settings))))

    def test_recovery_installation_checks_broker_code_role_activation_and_schedule(self):
        self.driver.api = self.installation
        self.driver.recovery_ready()
        for field in ('CodeSha256', 'Role', 'State'):
            def changed(service, operation, **arguments):
                value = self.installation(service, operation, **arguments)
                if service == 'lambda':
                    value[field] = 'invalid'
                return value
            self.driver.api = changed
            with self.assertRaisesRegex(Failure, 'RECOVERY_INSTALLATION_NOT_READY'):
                self.driver.recovery_ready()
        self.driver.api = lambda *args, **kwargs: (_ for _ in ()).throw(Failure('ResourceNotFoundException', 'broker'))
        with self.assertRaisesRegex(Failure, 'ResourceNotFoundException'):
            self.driver.recovery_ready()

    def test_access_entry_or_readyz_alone_cannot_authorize_bootstrap(self):
        self.driver.kubectl = lambda arguments: 'no\n'
        with self.assertRaisesRegex(Failure, 'KUBERNETES_BOOTSTRAP_FORBIDDEN'):
            self.driver.bootstrap_permissions()
        self.assertFalse(self.driver.kubernetes_authorized())
        self.driver.kubectl = lambda arguments: 'yes\n'
        self.driver.bootstrap_permissions()

    def test_privileged_checkpoint_without_approval_never_invokes_broker(self):
        self.driver.invoke = lambda *args: self.fail('Unapproved bootstrap must not invoke Lambda')
        with self.assertRaisesRegex(Failure, 'BOOTSTRAP_NOT_AUTHORIZED'):
            with self.driver.kubernetes():
                self.fail('Unapproved bootstrap entered Kubernetes')

    def test_partial_recovery_rejects_uninventoried_data_volume_and_allows_verified_node_root(self):
        root_volume = dict(VolumeId='vol-root', Attachments=[dict(InstanceId='i-node')], Tags=[])
        self.driver.owned_volumes = lambda: [root_volume]
        self.driver.api = lambda *args, **kwargs: dict(nodegroup=dict(clusterName=PREFIX,
            tags=dict(EnvironmentId='demo-a'), resources=dict(autoScalingGroups=[dict(name='owned-asg')])))
        self.driver.items = lambda *args, **kwargs: [dict(Instances=[dict(InstanceId='i-node', RootDeviceName='/dev/xvda',
            Tags=[dict(Key='EnvironmentId', Value='demo-a'), dict(Key='aws:autoscaling:groupName', Value='owned-asg')],
            BlockDeviceMappings=[dict(DeviceName='/dev/xvda', Ebs=dict(VolumeId='vol-root', DeleteOnTermination=True))])])]
        self.assertEqual(self.driver.non_node_volumes(), [])
        root_volume['VolumeId'] = 'vol-data-without-inventory'
        self.assertEqual(len(self.driver.non_node_volumes()), 1)
        root_volume['VolumeId'] = 'vol-root'
        root_volume['Tags'] = [dict(Key='kubernetes.io/created-for/pvc/name', Value='mongodb')]
        self.assertEqual(len(self.driver.non_node_volumes()), 1)

    def test_closure_identity_does_not_depend_on_s3_or_terraform(self):
        calls = []
        def identity(service, operation, **arguments):
            calls.append(service)
            if service != 'sts':
                self.fail('Closure must not load the backend')
            return dict(Account=ACCOUNT, Arn=f'arn:aws:sts::{ACCOUNT}:assumed-role/{PREFIX}-provision/test')
        self.driver.api = identity
        with patch('pipeline.ROOT', Path(self.temporary.name)), patch.dict(os.environ, EXECUTOR_ROLE='provision'):
            self.driver.load_closure()
        self.assertEqual(calls, ['sts'])

    def test_lambda_authorization_error_keeps_sanitized_cause(self):
        self.driver.api = lambda *args, **kwargs: dict(FunctionError='Unhandled', Payload=io.BytesIO(json.dumps(
            dict(errorType='ClientError', errorMessage='AccessDeniedException eks:AssociateAccessPolicy token=synthetic-secret')).encode()))
        with self.assertRaises(Failure) as caught:
            self.driver.invoke('broker', {'bootstrap': 'a' * 32})
        self.assertIn('eks:AssociateAccessPolicy', caught.exception.detail['message'])
        self.assertNotIn('synthetic-secret', caught.exception.detail['message'])


if __name__ == '__main__':
    unittest.main()
