import copy
import io
import json
import sys
import threading
import unittest
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from recovery import Blocked, EndpointLease, Pending, Store, recovery_handler


class AwsError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class Dynamo:
    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()
        self.fail = False

    def get_item(self, **arguments):
        assert arguments["ConsistentRead"] is True
        with self.lock:
            item = self.items.get(arguments["Key"]["id"]["S"])
            return {"Item": copy.deepcopy(item)} if item else {}

    def transact_write_items(self, TransactItems):
        with self.lock:
            if self.fail:
                raise AwsError("InternalServerError")
            for operation in TransactItems:
                arguments = operation["Put"]
                key = arguments["Item"]["id"]["S"]
                existing = self.items.get(key)
                condition = arguments["ConditionExpression"]
                values = arguments.get("ExpressionAttributeValues", {})
                if condition == "lease_id = :lease AND version = :version":
                    accepted = existing and existing["lease_id"] == values[":lease"] and existing["version"] == values[":version"]
                elif "OR" in condition:
                    accepted = not existing or existing["state"]["S"] in {"CLOSED", "MISSING"}
                else:
                    accepted = not existing
                if not accepted:
                    raise AwsError("TransactionCanceledException")
            for operation in TransactItems:
                item = operation["Put"]["Item"]
                self.items[item["id"]["S"]] = copy.deepcopy(item)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000
        self.config = {
            "environment_id": "demo-a", "account": "123456789012", "region": "us-east-1",
            "cluster_name": "tc-oficina-p4-demo-a",
            "cluster_arn": "arn:aws:eks:us-east-1:123456789012:cluster/tc-oficina-p4-demo-a",
            "role_name": "tc-oficina-p4-demo-a-endpoint-broker", "enabled": True, "recovery_verified": True,
            "schedule_group": "tc-oficina-p4-demo-a-recovery",
            "reconcile_interval_minutes": 5,
            "recovery_arn": "arn:aws:lambda:us-east-1:123456789012:function:tc-oficina-p4-demo-a-endpoint-recovery",
            "scheduler_role_arn": "arn:aws:iam::123456789012:role/tc-oficina-p4-demo-a-recovery-scheduler",
            "recovery_role_arn": "arn:aws:iam::123456789012:role/tc-oficina-p4-demo-a-endpoint-recovery",
            "dlq_arn": "arn:aws:sqs:us-east-1:123456789012:tc-oficina-p4-demo-a-endpoint-recovery-dlq",
            "code_sha256": "reviewed-code-hash"
        }
        self.dynamo = Dynamo()
        self.store = Store(self.dynamo, "leases")
        self.network = {"endpointPublicAccess": False, "endpointPrivateAccess": True}
        self.cluster_value = {"arn": self.config["cluster_arn"], "status": "ACTIVE",
                              "createdAt": datetime(2026, 11, 3, tzinfo=timezone.utc),
                              "tags": {"EnvironmentId": "demo-a"}, "resourcesVpcConfig": self.network}
        self.eks = Mock(meta=SimpleNamespace(region_name="us-east-1"))
        self.eks.describe_cluster.side_effect = lambda **arguments: {"cluster": copy.deepcopy(self.cluster_value)}
        self.eks.update_cluster_config.return_value = {"update": {"id": "update-123"}}
        self.eks.describe_update.return_value = {"update": {"status": "Successful"}}
        self.schedules = {}
        self.scheduler = Mock()
        self.scheduler.create_schedule.side_effect = self.create_schedule
        self.scheduler.get_schedule.side_effect = lambda Name, GroupName: copy.deepcopy(self.schedules[Name])
        self.functions = Mock()
        self.functions.get_function_configuration.return_value = {
            "State": "Active", "LastUpdateStatus": "Successful", "Role": self.config["recovery_role_arn"],
            "CodeSha256": self.config["code_sha256"]}
        self.functions.invoke.side_effect = lambda **arguments: {"Payload": io.BytesIO(b'{"verified":true}')}
        self.sts = Mock()
        self.sts.get_caller_identity.return_value = {
            "Account": self.config["account"],
            "Arn": f"arn:aws:sts::{self.config['account']}:assumed-role/{self.config['role_name']}/test"}
        self.service = EndpointLease(self.config, self.store, self.eks, self.scheduler, self.functions,
                                     self.sts, clock=lambda: self.now, sleep=lambda seconds: None)
        self.schedules["reconcile"] = {"State": "ENABLED", "ScheduleExpression": "rate(5 minutes)",
            "ScheduleExpressionTimezone": "UTC", "FlexibleTimeWindow": {"Mode": "OFF"},
            "ActionAfterCompletion": "NONE", "Target": self.service.target({"reconcile": True})}
        self.request = {"github_run_id": "12345", "revision": "a" * 40, "observed_ip": "8.8.8.8", "duration_seconds": 900}

    def create_schedule(self, **arguments):
        name = arguments["Name"]
        if name in self.schedules:
            raise AwsError("ConflictException")
        self.schedules[name] = copy.deepcopy(arguments)
        return {"ScheduleArn": name}

    def prepare(self, **changes):
        return self.service.prepare({**self.request, **changes})["lease_id"]

    def test_valid_lease_and_exact_open_payload(self):
        lease_id = self.prepare()
        self.service.open(lease_id)
        request = self.eks.update_cluster_config.call_args.kwargs
        self.assertEqual(request["name"], self.config["cluster_name"])
        self.assertEqual(request["resourcesVpcConfig"], {
            "endpointPublicAccess": True, "endpointPrivateAccess": True, "publicAccessCidrs": ["8.8.8.8/32"]})
        self.assertEqual(self.store.get(lease_id)["state"], "OPEN_REQUESTED")

    def test_invalid_inputs_never_persist(self):
        for changes in [{"observed_ip": value} for value in ["0.0.0.0/0", "8.8.8.8/24", "::1", "127.0.0.1", "10.0.0.1", "224.1.1.1", "100.64.0.1"]] + [
            {"duration_seconds": 1801}, {"duration_seconds": 299}, {"github_run_id": "foreign"}, {"revision": "bad"}]:
            with self.subTest(changes=changes), self.assertRaises(Blocked):
                self.prepare(**changes)
        self.assertEqual(self.dynamo.items, {})

    def test_identity_rejections(self):
        for field, value in [("account", "999999999999"), ("region", "us-west-2"),
                             ("cluster_arn", "foreign"), ("cluster_name", "foreign"), ("environment_id", "demo-b")]:
            with self.subTest(field=field), patch.dict(self.config, {field: value}), self.assertRaises(Blocked):
                self.prepare()
        self.eks.meta.region_name = "us-west-2"
        with self.assertRaises(Blocked):
            self.prepare()
        self.eks.update_cluster_config.assert_not_called()

    def test_foreign_observed_cluster_and_ownership(self):
        for change in [{"arn": "foreign"}, {"tags": {"EnvironmentId": "demo-b"}}]:
            with patch.dict(self.cluster_value, change), self.assertRaises(Blocked):
                self.prepare()
        self.eks.update_cluster_config.assert_not_called()

    def test_persistence_failure_blocks_open(self):
        self.dynamo.fail = True
        with self.assertRaises(AwsError):
            self.prepare()
        self.scheduler.create_schedule.assert_not_called()
        self.eks.update_cluster_config.assert_not_called()

    def test_scheduler_failure_leaves_durable_lock_and_blocks_open(self):
        self.scheduler.create_schedule.side_effect = AwsError("AccessDeniedException")
        with self.assertRaises(AwsError):
            self.prepare()
        record = self.store.get("CURRENT")
        self.assertEqual(record["state"], "PREPARING")
        with self.assertRaises(Blocked):
            self.service.open(record["lease_id"])
        self.eks.update_cluster_config.assert_not_called()

    def test_schedule_tampering_blocks_open(self):
        lease_id = self.prepare()
        for mutation in [lambda schedule: schedule.update(State="DISABLED"),
                         lambda schedule: schedule["Target"].update(Arn="foreign"),
                         lambda schedule: schedule["Target"].update(RoleArn="foreign"),
                         lambda schedule: schedule["Target"].update(DeadLetterConfig={}),
                         lambda schedule: schedule.update(ScheduleExpression="at(2099-01-01T00:00:00)")]:
            original = copy.deepcopy(self.schedules["lease-" + lease_id])
            mutation(self.schedules["lease-" + lease_id])
            with self.assertRaises(Blocked):
                self.service.open(lease_id)
            self.schedules["lease-" + lease_id] = original
        self.eks.update_cluster_config.assert_not_called()

    def test_missing_reconciliation_blocks_open(self):
        lease_id = self.prepare()
        del self.schedules["reconcile"]
        with self.assertRaises(KeyError):
            self.service.open(lease_id)
        self.eks.update_cluster_config.assert_not_called()

    def test_configured_reconciliation_intervals(self):
        for interval, expression in [(1, "rate(1 minute)"), (3, "rate(3 minutes)"), (5, "rate(5 minutes)")]:
            with self.subTest(interval=interval):
                self.setUp()
                self.config["reconcile_interval_minutes"] = interval
                self.schedules["reconcile"]["ScheduleExpression"] = expression
                lease_id = self.prepare()
                self.service.open(lease_id)
                self.assertEqual(self.store.get(lease_id)["state"], "OPEN_REQUESTED")

    def test_invalid_reconciliation_interval_blocks_open(self):
        lease_id = self.prepare()
        for interval in [0, 6, 1.5, True, "5", None]:
            with self.subTest(interval=interval), patch.dict(self.config, {"reconcile_interval_minutes": interval}):
                with self.assertRaisesRegex(Blocked, "RECONCILE_INTERVAL_REJECTED"):
                    self.service.open(lease_id)
        self.eks.update_cluster_config.assert_not_called()

    def test_mismatched_reconciliation_interval_blocks_open(self):
        lease_id = self.prepare()
        self.schedules["reconcile"]["ScheduleExpression"] = "rate(1 minute)"
        with self.assertRaisesRegex(Blocked, "SCHEDULE_REJECTED"):
            self.service.open(lease_id)
        self.eks.update_cluster_config.assert_not_called()

    def test_idle_reconciliation_succeeds_without_cluster_or_lease(self):
        context = Mock()
        context.get_remaining_time_in_millis.return_value = 30000
        for terminal in [None, "CLOSED", "MISSING"]:
            with self.subTest(terminal=terminal):
                if terminal:
                    lease_id = self.prepare(github_run_id="12345" if terminal == "CLOSED" else "54321")
                    self.store.change(self.store.get(lease_id), state=terminal)
                self.eks.describe_cluster.reset_mock()
                with patch("recovery.runtime", return_value=self.service):
                    self.assertEqual(recovery_handler({"reconcile": True}, context), {"state": "IDLE"})
                self.eks.describe_cluster.assert_not_called()
        self.eks.update_cluster_config.assert_not_called()

    def test_failed_probe_and_wrong_code_block_open(self):
        lease_id = self.prepare()
        self.functions.invoke.return_value = None
        self.functions.invoke.side_effect = lambda **arguments: {"FunctionError": "Unhandled"}
        with self.assertRaises(Blocked):
            self.service.open(lease_id)
        self.functions.get_function_configuration.return_value["CodeSha256"] = "unreviewed"
        with self.assertRaises(Blocked):
            self.service.open(lease_id)
        self.eks.update_cluster_config.assert_not_called()

    def test_concurrent_workflows_have_one_winner(self):
        def attempt(run):
            try:
                return self.prepare(github_run_id=str(run))
            except AwsError:
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, [100, 200]))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(len(self.dynamo.items), 2)

    def test_rerun_does_not_extend_expiry(self):
        lease_id = self.prepare()
        expiry = self.store.get(lease_id)["expires_at"]
        self.now += 120
        self.assertEqual(self.prepare(duration_seconds=1800), lease_id)
        self.assertEqual(self.store.get(lease_id)["expires_at"], expiry)
        with self.assertRaises(Blocked):
            self.prepare(observed_ip="8.8.4.4")

    def test_closed_run_cannot_be_reused(self):
        lease_id = self.prepare()
        self.service.close(lease_id)
        with self.assertRaises(Blocked):
            self.prepare()
        self.prepare(github_run_id="54321")
        self.assertEqual(self.service.close(lease_id), {"state": "CLOSED"})
        self.assertEqual(self.store.get("CURRENT")["github_run_id"], "54321")

    def test_expired_lease_and_expiration_during_verification(self):
        lease_id = self.prepare()
        def probe(**arguments):
            self.now += 901
            return {"Payload": io.BytesIO(b'{"verified":true}')}
        self.functions.invoke.side_effect = probe
        with self.assertRaises(Blocked):
            self.service.open(lease_id)
        with self.assertRaises(Blocked):
            self.service.open(lease_id)
        self.eks.update_cluster_config.assert_not_called()

    def test_disabled_bootstrap_never_opens(self):
        for field in ["enabled", "recovery_verified"]:
            with patch.dict(self.config, {field: False}), self.assertRaises(Blocked):
                self.prepare()

    def test_close_private_is_idempotent(self):
        lease_id = self.prepare()
        self.assertEqual(self.service.close(lease_id), {"state": "CLOSED"})
        self.assertEqual(self.service.close(lease_id), {"state": "CLOSED"})
        self.assertIn("verified_at", self.store.get(lease_id))
        self.eks.update_cluster_config.assert_not_called()

    def test_close_async_only_confirms_final_private_state(self):
        lease_id = self.prepare()
        self.network["endpointPublicAccess"] = True
        with self.assertRaises(Pending):
            self.service.close(lease_id)
        self.assertEqual(self.eks.update_cluster_config.call_args.kwargs["resourcesVpcConfig"], {
            "endpointPublicAccess": False, "endpointPrivateAccess": True})
        self.assertEqual(self.store.get(lease_id)["state"], "CLOSING")
        self.eks.describe_update.return_value = {"update": {"status": "InProgress"}}
        with self.assertRaises(Pending):
            self.service.close(lease_id)
        self.network["endpointPublicAccess"] = False
        self.eks.describe_update.return_value = {"update": {"status": "Successful"}}
        self.assertEqual(self.service.close(lease_id), {"state": "CLOSED"})

    def test_existing_update_retries_without_mutation(self):
        lease_id = self.prepare()
        self.cluster_value["status"] = "UPDATING"
        with self.assertRaises(Pending):
            self.service.close(lease_id)
        self.eks.update_cluster_config.assert_not_called()
        self.cluster_value["status"] = "ACTIVE"
        self.assertEqual(self.service.close(lease_id), {"state": "CLOSED"})

    def test_pending_open_cannot_be_mistaken_for_final_private_state(self):
        lease_id = self.prepare()
        self.service.open(lease_id)
        self.now += 121
        self.eks.describe_update.return_value = {"update": {"status": "InProgress"}}
        with self.assertRaises(Pending):
            self.service.close(lease_id)
        self.assertEqual(self.store.get(lease_id)["state"], "CLOSING")
        self.assertEqual(self.eks.update_cluster_config.call_count, 1)
        self.eks.describe_update.return_value = {"update": {"status": "Successful"}}
        self.assertEqual(self.service.close(lease_id), {"state": "CLOSED"})

    def test_temporary_api_error_is_retryable_without_success(self):
        lease_id = self.prepare()
        self.eks.describe_cluster.side_effect = AwsError("ServiceUnavailableException")
        with self.assertRaises(AwsError):
            self.service.close(lease_id)
        self.assertEqual(self.store.get(lease_id)["state"], "CLOSING")
        self.eks.describe_cluster.side_effect = lambda **arguments: {"cluster": copy.deepcopy(self.cluster_value)}
        self.assertEqual(self.service.close(lease_id), {"state": "CLOSED"})

    def test_failed_eks_update_is_not_success(self):
        lease_id = self.prepare()
        self.network["endpointPublicAccess"] = True
        with self.assertRaises(Pending):
            self.service.close(lease_id)
        self.eks.describe_update.return_value = {"update": {"status": "Failed"}}
        with self.assertRaises(Pending):
            self.service.close(lease_id)
        self.assertEqual(self.store.get(lease_id)["state"], "CLOSING")
        self.assertIsNone(self.store.get(lease_id)["close_update_id"])

    def test_runner_interrupted_before_open_is_reconciled(self):
        lease_id = self.prepare()
        self.assertEqual(self.service.recover({"reconcile": True}), {"state": "WAITING"})
        self.now += 901
        self.assertEqual(self.service.recover({"reconcile": True}), {"state": "CLOSED"})
        self.assertEqual(self.store.get(lease_id)["state"], "CLOSED")

    def test_runner_interrupted_after_open_closes_at_expiry(self):
        lease_id = self.prepare()
        self.service.open(lease_id)
        self.network["endpointPublicAccess"] = True
        self.now += 901
        with self.assertRaises(Pending):
            self.service.recover({"reconcile": True})
        self.network["endpointPublicAccess"] = False
        self.assertEqual(self.service.recover({"lease_id": lease_id}), {"state": "CLOSED"})

    def test_dispatch_fence_blocks_premature_success(self):
        lease_id = self.prepare()
        self.service.open(lease_id)
        with self.assertRaises(Pending):
            self.service.close(lease_id)
        with self.assertRaises(Blocked):
            self.service.open(lease_id)
        self.assertEqual(self.store.get(lease_id)["state"], "CLOSING")
        self.now += 121
        self.assertEqual(self.service.close(lease_id), {"state": "CLOSED"})

    def test_ambiguous_open_keeps_lock_even_when_currently_private(self):
        lease_id = self.prepare()
        self.eks.update_cluster_config.side_effect = AwsError("ServiceUnavailableException")
        with self.assertRaises(AwsError):
            self.service.open(lease_id)
        self.now += 901
        with self.assertRaises(Pending):
            self.service.close(lease_id)
        self.assertTrue(self.store.get(lease_id)["dispatch_uncertain"])
        self.assertEqual(self.store.get(lease_id)["state"], "CLOSING")
        with self.assertRaises(AwsError):
            self.prepare(github_run_id="54321")

    def test_stale_write_cannot_overwrite_closing(self):
        lease_id = self.prepare()
        original = self.store.get(lease_id)
        self.store.change(original, state="CLOSING")
        with self.assertRaises(AwsError):
            self.store.change(original, state="OPEN_REQUESTED")

    def test_destroyed_cluster_is_missing_not_closed(self):
        lease_id = self.prepare()
        self.eks.describe_cluster.side_effect = AwsError("ResourceNotFoundException")
        self.assertEqual(self.service.close(lease_id), {"state": "MISSING"})
        self.eks.update_cluster_config.assert_not_called()

    def test_foreign_record_cannot_modify_eks(self):
        lease_id = self.prepare()
        record = self.store.get(lease_id)
        self.store.change(record, cluster_arn=record["cluster_arn"].replace("demo-a", "demo-b"))
        with self.assertRaises(Blocked):
            self.service.close(lease_id)
        self.eks.update_cluster_config.assert_not_called()

    def test_payload_is_not_authorization(self):
        for event in [{"lease_id": "unknown"}, {"lease_id": "unknown", "cluster_name": "foreign"}]:
            with self.assertRaises(Blocked):
                self.service.recover(event)
        self.eks.update_cluster_config.assert_not_called()

    def test_probe_requires_independent_recent_canary_bound_to_code_and_cluster(self):
        with self.assertRaises(Blocked):
            self.service.recover({"probe": True})
        evidence = {key: self.config[key] for key in [
            "environment_id", "account", "region", "cluster_arn", "recovery_role_arn", "code_sha256"]}
        evidence.update(verified_at=self.now - 60, valid_until=self.now + 600, close_update_id="canary",
                        cluster_created_at=self.cluster_value["createdAt"].isoformat())
        self.dynamo.items["VERIFICATION"] = {"record": {"S": json.dumps(evidence)}}
        self.assertEqual(self.service.recover({"probe": True}), {"verified": True})
        for change in [{"code_sha256": "forged"}, {"valid_until": self.now}, {"cluster_created_at": "foreign"}]:
            self.dynamo.items["VERIFICATION"] = {"record": {"S": json.dumps({**evidence, **change})}}
            with self.assertRaises(Blocked):
                self.service.recover({"probe": True})
        self.eks.update_cluster_config.assert_not_called()

    def test_duplicate_recovery_observes_same_pending_update(self):
        lease_id = self.prepare()
        self.network["endpointPublicAccess"] = True
        with self.assertRaises(Pending):
            self.service.close(lease_id)
        self.eks.describe_update.return_value = {"update": {"status": "InProgress"}}
        with self.assertRaises(Pending):
            self.service.close(lease_id)
        self.assertEqual(self.eks.update_cluster_config.call_count, 1)

    def test_private_access_repaired_on_close(self):
        lease_id = self.prepare()
        self.network["endpointPrivateAccess"] = False
        with self.assertRaises(Pending):
            self.service.close(lease_id)
        self.assertTrue(self.eks.update_cluster_config.call_args.kwargs["resourcesVpcConfig"]["endpointPrivateAccess"])

    def test_definitive_failure_propagates_to_async_failure_destination(self):
        lease_id = self.prepare()
        self.eks.describe_cluster.side_effect = AwsError("AccessDeniedException")
        context = Mock()
        context.get_remaining_time_in_millis.return_value = 30000
        with patch("recovery.runtime", return_value=self.service), self.assertRaises(AwsError):
            recovery_handler({"lease_id": lease_id}, context)
        self.assertEqual(self.store.get(lease_id)["state"], "CLOSING")


if __name__ == "__main__":
    sys.modules["boto3"] = None
    unittest.main()
