import hashlib
import ipaddress
import json
import os
import re
import time
from datetime import datetime, timezone


class Blocked(RuntimeError):
    pass


class Pending(RuntimeError):
    pass


TERMINAL = {"CLOSED", "MISSING"}


def require(condition, code):
    if not condition:
        raise Blocked(code)


def error_code(error):
    return getattr(error, "response", {}).get("Error", {}).get("Code", "UNKNOWN")


class Store:
    def __init__(self, client, table):
        self.client = client
        self.table = table

    def get(self, key):
        item = self.client.get_item(TableName=self.table, Key={"id": {"S": key}}, ConsistentRead=True).get("Item")
        return json.loads(item["record"]["S"]) if item else None

    def put(self, record, previous=None):
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
        writes = []
        for key in ["CURRENT", record["lease_id"]]:
            arguments = {"TableName": self.table, "Item": {
                "id": {"S": key}, "record": {"S": encoded},
                "version": {"N": str(record["version"])},
                "lease_id": {"S": record["lease_id"]}, "state": {"S": record["state"]}}}
            if previous:
                arguments.update(ConditionExpression="lease_id = :lease AND version = :version",
                                 ExpressionAttributeValues={":lease": {"S": previous["lease_id"]},
                                                            ":version": {"N": str(previous["version"])}})
            elif key == "CURRENT":
                arguments.update(ConditionExpression="attribute_not_exists(id) OR #state IN (:closed, :missing)",
                                 ExpressionAttributeNames={"#state": "state"},
                                 ExpressionAttributeValues={":closed": {"S": "CLOSED"}, ":missing": {"S": "MISSING"}})
            else:
                arguments["ConditionExpression"] = "attribute_not_exists(id)"
            writes.append({"Put": arguments})
        self.client.transact_write_items(TransactItems=writes)
        return record

    def change(self, previous, **changes):
        return self.put({**previous, **changes, "version": previous["version"] + 1}, previous)


class EndpointLease:
    def __init__(self, config, store, eks, scheduler, functions, sts, clock=time.time, sleep=time.sleep):
        self.config = config
        self.store = store
        self.eks = eks
        self.scheduler = scheduler
        self.functions = functions
        self.sts = sts
        self.clock = clock
        self.sleep = sleep

    def identity(self):
        config = self.config
        require(config["environment_id"] in {"demo-a", "demo-b"}, "ENVIRONMENT_REJECTED")
        require(re.fullmatch(r"[0-9]{12}", config["account"]) is not None, "ACCOUNT_REJECTED")
        require(config["region"] == "us-east-1", "REGION_REJECTED")
        name = "tc-oficina-p4-" + config["environment_id"]
        require(config["cluster_name"] == name, "CLUSTER_REJECTED")
        require(config["cluster_arn"] == f"arn:aws:eks:{config['region']}:{config['account']}:cluster/{name}", "CLUSTER_REJECTED")
        observed = self.sts.get_caller_identity()
        require(observed["Account"] == config["account"], "OBSERVED_ACCOUNT_REJECTED")
        require(self.eks.meta.region_name == config["region"], "OBSERVED_REGION_REJECTED")
        role = config["role_name"]
        require(observed["Arn"].startswith(f"arn:aws:sts::{config['account']}:assumed-role/{role}/"), "ROLE_REJECTED")

    def validate(self, record):
        for field in ["environment_id", "account", "region", "cluster_name", "cluster_arn"]:
            require(record[field] == self.config[field], field.upper() + "_REJECTED")
        require(re.fullmatch(r"[0-9a-f]{32}", record["lease_id"]) is not None, "LEASE_REJECTED")
        require(re.fullmatch(r"[0-9]{1,20}", record["github_run_id"]) is not None, "RUN_REJECTED")
        require(re.fullmatch(r"[0-9a-f]{40}", record["revision"]) is not None, "REVISION_REJECTED")
        expected = hashlib.sha256((record["cluster_arn"] + ":" + record["github_run_id"]).encode()).hexdigest()[:32]
        require(record["lease_id"] == expected, "LEASE_REJECTED")
        require(record["schedule_id"] == "lease-" + expected, "SCHEDULE_REJECTED")
        try:
            network = ipaddress.ip_network(record["cidr"], strict=True)
            require(network.version == 4 and network.prefixlen == 32 and network.network_address.is_global
                    and not network.network_address.is_multicast, "CIDR_REJECTED")
        except ValueError:
            raise Blocked("CIDR_REJECTED") from None
        require(300 <= record["expires_at"] - record["created_at"] <= 1800, "DURATION_REJECTED")
        require(record["created_at"] <= self.clock() + 5, "CREATION_REJECTED")
        require(record["state"] in {"PREPARING", "ARMED", "DISPATCHING", "OPEN_REQUESTED", "CLOSING", *TERMINAL}, "STATE_REJECTED")
        require(isinstance(record["version"], int) and record["version"] > 0, "VERSION_REJECTED")

    def current(self, record):
        current = self.store.get("CURRENT")
        require(current is not None and current["lease_id"] == record["lease_id"]
                and current["version"] == record["version"], "STALE_LEASE")

    def cluster(self):
        cluster = self.eks.describe_cluster(name=self.config["cluster_name"])["cluster"]
        require(cluster["arn"] == self.config["cluster_arn"], "CLUSTER_REJECTED")
        require(cluster.get("tags", {}).get("EnvironmentId") == self.config["environment_id"], "OWNERSHIP_REJECTED")
        return cluster

    def target(self, payload):
        return {"Arn": self.config["recovery_arn"], "RoleArn": self.config["scheduler_role_arn"],
                "Input": json.dumps(payload, sort_keys=True, separators=(",", ":")),
                "DeadLetterConfig": {"Arn": self.config["dlq_arn"]},
                "RetryPolicy": {"MaximumEventAgeInSeconds": 3600, "MaximumRetryAttempts": 10}}

    def verify_schedule(self, name, expression, payload):
        result = self.scheduler.get_schedule(Name=name, GroupName=self.config["schedule_group"])
        require(result["State"] == "ENABLED" and result["ScheduleExpression"] == expression
                and result.get("ScheduleExpressionTimezone", "UTC") == "UTC"
                and result["FlexibleTimeWindow"] == {"Mode": "OFF"}, "SCHEDULE_REJECTED")
        expected = self.target(payload)
        target = result["Target"]
        require(all(target.get(key) == value for key, value in expected.items()), "TARGET_REJECTED")
        require(result.get("ActionAfterCompletion", "NONE") == "NONE", "SCHEDULE_RETENTION_REJECTED")

    def expression(self, record):
        return "at(" + datetime.fromtimestamp(record["expires_at"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ")"

    def reconcile_expression(self):
        interval = self.config.get("reconcile_interval_minutes")
        require(type(interval) is int and 1 <= interval <= 5, "RECONCILE_INTERVAL_REJECTED")
        return f"rate({interval} {'minute' if interval == 1 else 'minutes'})"

    def prepare(self, request):
        self.identity()
        require(self.config["enabled"] and self.config["recovery_verified"], "OPENING_DISABLED")
        require(set(request) == {"github_run_id", "revision", "observed_ip", "duration_seconds"}, "INPUT_REJECTED")
        require(type(request["duration_seconds"]) is int, "DURATION_REJECTED")
        now = int(self.clock())
        lease_id = hashlib.sha256((self.config["cluster_arn"] + ":" + str(request["github_run_id"])).encode()).hexdigest()[:32]
        record = {key: self.config[key] for key in ["environment_id", "account", "region", "cluster_name", "cluster_arn"]}
        record.update(lease_id=lease_id, github_run_id=str(request["github_run_id"]), revision=request["revision"],
                      cidr=request["observed_ip"] + "/32", created_at=now, expires_at=now + request["duration_seconds"],
                      state="PREPARING", schedule_id="lease-" + lease_id, version=1, dispatch_until=0)
        self.validate(record)
        existing = self.store.get(lease_id)
        if existing:
            self.validate(existing)
            require(existing["revision"] == record["revision"] and existing["cidr"] == record["cidr"], "REEXECUTION_CHANGED")
            require(existing["state"] in {"PREPARING", "ARMED"} and existing["expires_at"] - now >= 120, "REEXECUTION_BLOCKED")
            record = existing
        else:
            cluster = self.cluster()
            require(cluster["status"] == "ACTIVE" and not cluster["resourcesVpcConfig"]["endpointPublicAccess"]
                    and cluster["resourcesVpcConfig"]["endpointPrivateAccess"], "CLUSTER_NOT_PRIVATE")
            record = self.store.put(record)
        payload = {"lease_id": lease_id}
        try:
            self.scheduler.create_schedule(Name=record["schedule_id"], GroupName=self.config["schedule_group"],
                ScheduleExpression=self.expression(record), ScheduleExpressionTimezone="UTC", State="ENABLED",
                FlexibleTimeWindow={"Mode": "OFF"}, ActionAfterCompletion="NONE", Target=self.target(payload),
                ClientToken=lease_id)
        except Exception as error:
            if error_code(error) != "ConflictException":
                raise
        self.verify_schedule(record["schedule_id"], self.expression(record), payload)
        if record["state"] == "PREPARING":
            record = self.store.change(record, state="ARMED")
        return {"lease_id": lease_id, "expires_at": record["expires_at"], "state": record["state"]}

    def open(self, lease_id):
        self.identity()
        require(self.config["enabled"] and self.config["recovery_verified"], "OPENING_DISABLED")
        record = self.store.get(lease_id)
        require(record is not None, "LEASE_NOT_FOUND")
        self.validate(record)
        self.current(record)
        require(record["state"] == "ARMED" and record["expires_at"] - self.clock() >= 120, "LEASE_NOT_ARMED")
        self.verify_schedule(record["schedule_id"], self.expression(record), {"lease_id": lease_id})
        self.verify_schedule("reconcile", self.reconcile_expression(), {"reconcile": True})
        configuration = self.functions.get_function_configuration(FunctionName=self.config["recovery_arn"])
        require(configuration["State"] == "Active" and configuration["LastUpdateStatus"] == "Successful"
                and configuration["Role"] == self.config["recovery_role_arn"]
                and configuration["CodeSha256"] == self.config["code_sha256"], "RECOVERY_NOT_READY")
        probe = self.functions.invoke(FunctionName=self.config["recovery_arn"], InvocationType="RequestResponse",
                                      Payload=b'{"probe":true}')
        require(not probe.get("FunctionError") and json.loads(probe["Payload"].read()) == {"verified": True}, "RECOVERY_PROBE_FAILED")
        cluster = self.cluster()
        require(cluster["status"] == "ACTIVE" and cluster["resourcesVpcConfig"]["endpointPrivateAccess"]
                and not cluster["resourcesVpcConfig"]["endpointPublicAccess"], "CLUSTER_NOT_PRIVATE")
        require(record["expires_at"] - self.clock() >= 120, "LEASE_EXPIRED")
        record = self.store.change(record, state="DISPATCHING", dispatch_until=int(self.clock()) + 120)
        require(record["expires_at"] - self.clock() >= 120, "LEASE_EXPIRED")
        update = self.eks.update_cluster_config(name=record["cluster_name"], clientRequestToken=lease_id,
            resourcesVpcConfig={"endpointPublicAccess": True, "endpointPrivateAccess": True, "publicAccessCidrs": [record["cidr"]]})["update"]
        self.store.change(record, state="OPEN_REQUESTED", update_id=update["id"])
        return {"lease_id": lease_id, "state": "OPEN_REQUESTED"}

    def close(self, lease_id):
        self.identity()
        record = self.store.get(lease_id)
        require(record is not None, "LEASE_NOT_FOUND")
        self.validate(record)
        if record["state"] in TERMINAL:
            return {"state": record["state"]}
        self.current(record)
        if record["state"] != "CLOSING":
            record = self.store.change(record, state="CLOSING", dispatch_uncertain=record["state"] == "DISPATCHING")
        if self.clock() < record["dispatch_until"]:
            raise Pending("DISPATCH_FENCE_ACTIVE")
        try:
            cluster = self.cluster()
        except Exception as error:
            if error_code(error) == "ResourceNotFoundException":
                self.store.change(record, state="MISSING", verified_at=int(self.clock()))
                return {"state": "MISSING"}
            raise
        if cluster["status"] != "ACTIVE":
            raise Pending("CLUSTER_UPDATE_IN_PROGRESS")
        if record.get("update_id"):
            opening = self.eks.describe_update(name=record["cluster_name"], updateId=record["update_id"])["update"]
            if opening["status"] not in {"Successful", "Failed", "Cancelled"}:
                raise Pending("OPEN_UPDATE_IN_PROGRESS")
            cluster = self.cluster()
            if cluster["status"] != "ACTIVE":
                raise Pending("CLUSTER_UPDATE_IN_PROGRESS")
        if record.get("close_update_id"):
            update = self.eks.describe_update(name=record["cluster_name"], updateId=record["close_update_id"])["update"]
            if update["status"] == "InProgress":
                raise Pending("CLOSE_UPDATE_IN_PROGRESS")
            if update["status"] != "Successful":
                record = self.store.change(record, close_update_id=None)
                raise Pending("CLOSE_UPDATE_FAILED_RETRY")
            cluster = self.cluster()
            if cluster["status"] != "ACTIVE":
                raise Pending("CLUSTER_UPDATE_IN_PROGRESS")
        network = cluster["resourcesVpcConfig"]
        if not network["endpointPublicAccess"] and network["endpointPrivateAccess"]:
            if record.get("dispatch_uncertain"):
                raise Pending("AMBIGUOUS_DISPATCH_REQUIRES_OPERATOR_REVIEW")
            self.store.change(record, state="CLOSED", verified_at=int(self.clock()))
            return {"state": "CLOSED"}
        update = self.eks.update_cluster_config(name=record["cluster_name"],
            clientRequestToken=hashlib.sha256((lease_id + ":close:" + str(record["version"])).encode()).hexdigest(),
            resourcesVpcConfig={"endpointPublicAccess": False, "endpointPrivateAccess": True})["update"]
        self.store.change(record, close_update_id=update["id"])
        raise Pending("CLOSE_UPDATE_REQUESTED")

    def recover(self, event):
        self.identity()
        if event == {"probe": True}:
            evidence = self.store.get("VERIFICATION")
            require(evidence is not None, "RECOVERY_NOT_VERIFIED")
            for field in ["environment_id", "account", "region", "cluster_arn", "recovery_role_arn", "code_sha256"]:
                require(evidence[field] == self.config[field], "RECOVERY_EVIDENCE_REJECTED")
            require(evidence["verified_at"] <= self.clock() < evidence["valid_until"]
                    and 0 < evidence["valid_until"] - evidence["verified_at"] <= 86400, "RECOVERY_EVIDENCE_EXPIRED")
            update = self.eks.describe_update(name=self.config["cluster_name"], updateId=evidence["close_update_id"])["update"]
            require(update["status"] == "Successful", "RECOVERY_CANARY_FAILED")
            cluster = self.cluster()
            network = cluster["resourcesVpcConfig"]
            require(cluster["status"] == "ACTIVE" and not network["endpointPublicAccess"]
                    and network["endpointPrivateAccess"], "RECOVERY_CANARY_NOT_PRIVATE")
            require(evidence["cluster_created_at"] == cluster["createdAt"].isoformat(), "RECOVERY_CLUSTER_RECREATED")
            return {"verified": True}
        if event == {"reconcile": True}:
            record = self.store.get("CURRENT")
            if not record or record["state"] in TERMINAL:
                return {"state": "IDLE"}
            self.validate(record)
            if record["expires_at"] > self.clock() and record["state"] != "CLOSING":
                return {"state": "WAITING"}
            return self.close(record["lease_id"])
        require(set(event) == {"lease_id"}, "EVENT_REJECTED")
        return self.close(event["lease_id"])


def runtime(context, broker=False):
    import boto3
    from botocore.config import Config

    config = json.loads(os.environ["RECOVERY_CONFIG"])
    require(context.invoked_function_arn == config["broker_arn" if broker else "recovery_arn"], "FUNCTION_REJECTED")
    config["role_name"] = config["broker_role_name" if broker else "recovery_role_name"]
    options = Config(connect_timeout=3, read_timeout=5, retries={"mode": "standard", "total_max_attempts": 2})
    clients = {name: boto3.client(name, region_name=config["region"], config=options)
               for name in ["dynamodb", "eks", "scheduler", "lambda", "sts"]}
    return EndpointLease(config, Store(clients["dynamodb"], config["table"]), clients["eks"],
                         clients["scheduler"], clients["lambda"], clients["sts"])


def broker_handler(event, context):
    require(context.get_remaining_time_in_millis() <= 60000, "BROKER_TIMEOUT_REJECTED")
    service = runtime(context, broker=True)
    if set(event) == {"prepare"}:
        return service.prepare(event["prepare"])
    require(set(event) == {"open"}, "EVENT_REJECTED")
    return service.open(event["open"])


def recovery_handler(event, context):
    service = runtime(context)
    while context.get_remaining_time_in_millis() > 15000:
        try:
            result = service.recover(event)
            print(json.dumps({"operation": "endpoint-recovery", "result": result}))
            return result
        except Pending:
            service.sleep(5)
    raise Pending("RECOVERY_NOT_CONFIRMED")
