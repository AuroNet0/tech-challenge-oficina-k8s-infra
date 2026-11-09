import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime import ACCOUNT, PREFIX, Driver, Failure, Report
from verify_recovery import verify


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        package = self.root / 'phase4/bootstrap/identities/endpoint-recovery.zip'
        package.parent.mkdir(parents=True)
        package.write_bytes(b'offline-only-fixture')
        self.checksum = base64.b64encode(hashlib.sha256(package.read_bytes()).digest()).decode()
        self.settings = dict(account=ACCOUNT, region='us-east-1', environment_id='demo-a', code_sha256=self.checksum,
            table=PREFIX + '-endpoint-leases', schedule_group=PREFIX + '-recovery', reconcile_interval_minutes=5,
            scheduler_role_arn=f'arn:aws:iam::{ACCOUNT}:role/{PREFIX}-recovery-scheduler',
            dlq_arn=f'arn:aws:sqs:us-east-1:{ACCOUNT}:{PREFIX}-endpoint-recovery-dlq',
            recovery_role_arn=f'arn:aws:iam::{ACCOUNT}:role/{PREFIX}-endpoint-recovery')
        self.created = datetime(2026, 11, 8, tzinfo=timezone.utc)
        self.canary = dict(self.settings, state='VERIFIED', github_run_id='123', revision='a' * 40,
            cluster_arn=f'arn:aws:eks:us-east-1:{ACCOUNT}:cluster/{PREFIX}', cluster_created_at=self.created.isoformat(),
            requested_at=10, verified_at=20, close_update_id='private-canary')
        self.driver = Driver(dict(backend_bucket='unused-offline'), Report('canary', self.root / 'report'), sleep=lambda seconds: None)
        self.addCleanup(self.driver.temporary.cleanup)
        self.invocations = []
        self.writes = []
        self.driver.api = self.api
        self.driver.invoke = self.invoke
        self.driver.cluster = lambda: dict(createdAt=self.created, status='ACTIVE',
            resourcesVpcConfig=dict(endpointPublicAccess=False, endpointPrivateAccess=True))

    def invoke(self, purpose, payload):
        self.invocations.append((purpose, payload))
        if 'prepare' in payload:
            return dict(lease_id='b' * 32, expires_at=100)
        return dict(verified=True)

    def api(self, service, operation, **arguments):
        if service == 'sts':
            return dict(Account=ACCOUNT, Arn=f'arn:aws:sts::{ACCOUNT}:assumed-role/{PREFIX}-bootstrap/test')
        if service == 'lambda':
            return dict(CodeSha256=self.checksum, State='Active', LastUpdateStatus='Successful',
                Role=self.settings['recovery_role_arn'], Environment=dict(Variables=dict(RECOVERY_CONFIG=json.dumps(self.settings))))
        if service == 'scheduler':
            return dict(State='ENABLED', ScheduleExpression='rate(5 minutes)', FlexibleTimeWindow=dict(Mode='OFF'),
                Target=dict(Arn=f'arn:aws:lambda:us-east-1:{ACCOUNT}:function:{PREFIX}-endpoint-recovery',
                    RoleArn=self.settings['scheduler_role_arn'], Input='{"reconcile":true}', DeadLetterConfig=dict(Arn=self.settings['dlq_arn'])))
        if service == 'dynamodb' and operation == 'get_item':
            record = self.canary if arguments['Key']['id']['S'] == 'CANARY' else dict(state='CLOSED', verified_at=110,
                expires_at=100, close_update_id='independent-expiry')
            return dict(Item=dict(record=dict(S=json.dumps(record))))
        if service == 'dynamodb' and operation == 'put_item':
            self.writes.append(arguments)
            return {}
        if service == 'cloudwatch':
            return dict(MetricAlarms=[dict(StateValue='OK')] * 4)
        if service == 'eks':
            return dict(update=dict(status='Successful'))
        self.fail('Unexpected service invocation: ' + service)

    def test_private_canary_records_proof_only_after_real_readbacks(self):
        with patch('verify_recovery.ROOT', self.root), patch.dict(os.environ,
                GITHUB_RUN_ID='123', GITHUB_SHA='a' * 40, VERIFY_MODE='closed-canary'):
            verify(self.driver)
        self.assertEqual(len(self.writes), 1)
        self.assertTrue(all(purpose == 'recovery' for purpose, payload in self.invocations))
        proof = json.loads((self.root / 'report/verification.json').read_text())
        self.assertEqual(proof['proof'], 'PRIVATE_CLOSE_AND_INDEPENDENT_SCHEDULER')

    def test_code_mismatch_cannot_register_verification(self):
        self.settings['code_sha256'] = 'changed'
        with patch('verify_recovery.ROOT', self.root), self.assertRaisesRegex(Failure, 'RECOVERY_CODE_OR_SCOPE_REJECTED'):
            verify(self.driver)
        self.assertFalse(self.writes)
        self.assertFalse(self.invocations)

    def test_expiry_canary_never_issues_runner_close(self):
        clusters = iter([
            dict(createdAt=self.created, status='ACTIVE', resourcesVpcConfig=dict(endpointPublicAccess=False, endpointPrivateAccess=True)),
            dict(createdAt=self.created, status='ACTIVE', resourcesVpcConfig=dict(endpointPublicAccess=True, endpointPrivateAccess=True, publicAccessCidrs=['8.8.8.8/32'])),
            dict(createdAt=self.created, status='ACTIVE', resourcesVpcConfig=dict(endpointPublicAccess=False, endpointPrivateAccess=True))])
        self.driver.cluster = lambda: next(clusters)
        with patch('verify_recovery.ROOT', self.root), patch.dict(os.environ, GITHUB_RUN_ID='123', GITHUB_SHA='a' * 40, VERIFY_MODE='lease-expiry'), patch('urllib.request.urlopen', return_value=io.BytesIO(b'8.8.8.8\n')):
            verify(self.driver)
        self.assertFalse(any('lease_id' in payload for purpose, payload in self.invocations if purpose == 'recovery'))
        proof = json.loads((self.root / 'report/verification.json').read_text())
        self.assertEqual(proof['proof'], 'PUBLIC_32_CLOSED_AFTER_EXPIRY_WITHOUT_RUNNER_CLOSE')
        self.assertEqual(proof['close_update_id'], 'independent-expiry')


if __name__ == '__main__':
    unittest.main()
