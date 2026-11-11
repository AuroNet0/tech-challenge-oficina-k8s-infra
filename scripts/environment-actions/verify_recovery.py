import base64
import json
import os
import ipaddress
from pathlib import Path
import time
import urllib.request

from runtime import ACCOUNT, PREFIX, REGION, ROOT, Driver, Failure, Report, canonical, require


def verify(driver):
    identity = driver.api('sts', 'get_caller_identity')
    require(identity['Account'] == ACCOUNT and identity['Arn'].startswith(
        f'arn:aws:sts::{ACCOUNT}:assumed-role/{PREFIX}-bootstrap/'), 'BOOTSTRAP_IDENTITY_REJECTED')
    function = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{PREFIX}-endpoint-recovery'
    configuration = driver.api('lambda', 'get_function_configuration', FunctionName=function)
    settings = json.loads(configuration['Environment']['Variables']['RECOVERY_CONFIG'])
    from runtime import digest
    package = ROOT / 'phase4/bootstrap/identities/endpoint-recovery.zip'
    checksum = base64.b64encode(bytes.fromhex(digest(package.read_bytes()))).decode()
    require(configuration['CodeSha256'] == settings['code_sha256'] == checksum
        and configuration['State'] == 'Active' and configuration['LastUpdateStatus'] == 'Successful'
        and configuration['Role'] == f'arn:aws:iam::{ACCOUNT}:role/{PREFIX}-endpoint-recovery'
        and settings['account'] == ACCOUNT and settings['region'] == REGION and settings['environment_id'] == 'demo-a'
        and settings['table'] == PREFIX + '-endpoint-leases' and settings['schedule_group'] == PREFIX + '-recovery'
        and settings['recovery_role_arn'] == configuration['Role'], 'RECOVERY_CODE_OR_SCOPE_REJECTED')
    schedule = driver.api('scheduler', 'get_schedule', Name='reconcile', GroupName=settings['schedule_group'])
    interval = settings['reconcile_interval_minutes']
    require(type(interval) is int and 1 <= interval <= 5, 'RECOVERY_INTERVAL_REJECTED')
    expression = f"rate({interval} {'minute' if interval == 1 else 'minutes'})"
    target = schedule['Target']
    require(schedule['State'] == 'ENABLED' and schedule['ScheduleExpression'] == expression
        and schedule['FlexibleTimeWindow'] == {'Mode': 'OFF'} and target['Arn'] == function
        and target['RoleArn'] == settings['scheduler_role_arn'] and json.loads(target['Input']) == {'reconcile': True}
        and target['DeadLetterConfig']['Arn'] == settings['dlq_arn'], 'INDEPENDENT_SCHEDULE_REJECTED')
    driver.invoke('recovery', {'canary': dict(github_run_id=os.environ['GITHUB_RUN_ID'], revision=os.environ['GITHUB_SHA'])})
    alarm_names = [PREFIX + '-recovery-' + name for name in ('dlq', 'errors', 'destination', 'heartbeat')]
    canary = None
    for attempt in range(180):
        record = driver.api('dynamodb', 'get_item', TableName=settings['table'], Key={'id': {'S': 'CANARY'}}, ConsistentRead=True).get('Item')
        if record:
            canary = json.loads(record['record']['S'])
        alarms = driver.api('cloudwatch', 'describe_alarms', AlarmNames=alarm_names)['MetricAlarms']
        if canary and canary['state'] == 'VERIFIED' and len(alarms) == 4 and all(alarm['StateValue'] == 'OK' for alarm in alarms):
            break
        driver.sleep(10)
    else:
        raise Failure('INDEPENDENT_CANARY_OR_ALARMS_NOT_READY')
    cluster = driver.cluster()
    require(canary['github_run_id'] == os.environ['GITHUB_RUN_ID'] and canary['revision'] == os.environ['GITHUB_SHA']
        and canary['cluster_created_at'] == cluster['createdAt'].isoformat()
        and canary['code_sha256'] == checksum and canary['verified_at'] >= canary['requested_at'], 'CANARY_EVIDENCE_REJECTED')
    update = driver.api('eks', 'describe_update', name=PREFIX, updateId=canary['close_update_id'])['update']
    require(update['status'] == 'Successful' and not cluster['resourcesVpcConfig']['endpointPublicAccess']
        and cluster['resourcesVpcConfig']['endpointPrivateAccess'], 'CANARY_CLOSE_NOT_VERIFIED')
    fields = ['environment_id', 'account', 'region', 'cluster_arn', 'recovery_role_arn', 'code_sha256', 'cluster_created_at', 'close_update_id']
    verification = {key: canary[key] for key in fields}
    verification.update(verified_at=int(time.time()), valid_until=int(time.time()) + 86390)
    driver.api('dynamodb', 'put_item', TableName=settings['table'],
        Item={'id': {'S': 'VERIFICATION'}, 'record': {'S': canonical(verification).decode()}})
    require(driver.invoke('recovery', {'probe': True}) == {'verified': True}, 'REGISTERED_RECOVERY_PROBE_FAILED')
    verification['proof'] = 'PRIVATE_CLOSE_AND_INDEPENDENT_SCHEDULER'
    if os.getenv('VERIFY_MODE') == 'lease-expiry':
        with urllib.request.urlopen('https://checkip.amazonaws.com', timeout=10) as response:
            address = response.read(64).decode().strip()
        require(ipaddress.ip_address(address).version == 4 and ipaddress.ip_address(address).is_global, 'CANARY_IP_REJECTED')
        lease = driver.invoke('broker', {'prepare': dict(github_run_id=os.environ['GITHUB_RUN_ID'],
            github_run_attempt=os.getenv('GITHUB_RUN_ATTEMPT', '1'), revision=os.environ['GITHUB_SHA'],
            observed_ip=address, duration_seconds=300)})
        driver.lease = lease['lease_id']
        (driver.report.directory / 'endpoint-lease.json').write_bytes(canonical(dict(lease_id=driver.lease,
            run_id=os.environ['GITHUB_RUN_ID'], revision=os.environ['GITHUB_SHA'])))
        driver.invoke('broker', {'open': lease['lease_id']})
        opened = False
        terminal = None
        for attempt in range(120):
            cluster = driver.cluster()
            network = cluster['resourcesVpcConfig']
            if network['endpointPublicAccess'] and cluster['status'] == 'ACTIVE':
                require(network['endpointPrivateAccess'] and network['publicAccessCidrs'] == [address + '/32'], 'CANARY_CIDR_MISMATCH')
                opened = True
            item = driver.api('dynamodb', 'get_item', TableName=settings['table'],
                Key={'id': {'S': lease['lease_id']}}, ConsistentRead=True)['Item']
            terminal = json.loads(item['record']['S'])
            if opened and terminal['state'] == 'CLOSED' and not network['endpointPublicAccess'] and network['endpointPrivateAccess']:
                break
            driver.sleep(10)
        else:
            raise Failure('INDEPENDENT_LEASE_EXPIRY_NOT_PROVEN')
        require(terminal['verified_at'] >= terminal['expires_at'] and terminal.get('close_update_id'), 'INDEPENDENT_EXPIRY_EVIDENCE_REJECTED')
        verification.update(close_update_id=terminal['close_update_id'], proof='PUBLIC_32_CLOSED_AFTER_EXPIRY_WITHOUT_RUNNER_CLOSE', lease_id=lease['lease_id'])
        driver.api('dynamodb', 'put_item', TableName=settings['table'],
            Item={'id': {'S': 'VERIFICATION'}, 'record': {'S': canonical(verification).decode()}})
        require(driver.invoke('recovery', {'probe': True}) == {'verified': True}, 'EXPIRY_PROBE_FAILED')
    (driver.report.directory / 'verification.json').write_bytes(canonical(verification))


def main():
    report = Report('verify-recovery')
    driver = None
    try:
        with report.phase('authorize-and-prove-independent-recovery', PREFIX):
            require(os.getenv('GITHUB_REPOSITORY') == 'AuroNet0/tech-challenge-oficina-k8s-infra'
                and os.getenv('GITHUB_REF') == 'refs/heads/homolog' and os.getenv('GITHUB_EVENT_NAME') == 'workflow_dispatch'
                and os.getenv('CONFIRMATION') == 'VERIFY demo-a RECOVERY'
                and os.getenv('PHASE4_BOOTSTRAP_APPROVED') == 'true'
                and os.getenv('VERIFY_MODE', 'closed-canary') in ('closed-canary', 'lease-expiry'), 'RECOVERY_VERIFICATION_NOT_AUTHORIZED')
            configuration = json.loads((ROOT / 'phase4/environments/demo-a.runtime.json').read_text())
            driver = Driver(configuration, report)
            verify(driver)
    except Exception:
        return 1
    finally:
        if driver:
            driver.temporary.cleanup()
        report.finish()
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
