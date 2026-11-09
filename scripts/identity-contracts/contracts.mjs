import assert from 'node:assert/strict';
import { isIPv4 } from 'node:net';

export function subject(environment, purpose, format = 'immutable') {
    assert.ok(['demo-a', 'demo-b'].includes(environment), 'ENVIRONMENT_REJECTED');
    assert.ok(['provision', 'deploy', 'destroy'].includes(purpose), 'PURPOSE_REJECTED');
    assert.ok(['classic', 'immutable'].includes(format), 'SUBJECT_FORMAT_REJECTED');
    const repository = format === 'immutable'
        ? 'AuroNet0@128399450/tech-challenge-oficina-k8s-infra@1355187946'
        : 'AuroNet0/tech-challenge-oficina-k8s-infra';
    return `repo:${repository}:environment:phase4-${environment}-${purpose}`;
}

export function validateLease(catalog, request, now = new Date()) {
    const settings = catalog.environments[request.environment_id];
    assert.ok(settings?.enabled === true, 'ENVIRONMENT_DISABLED');
    assert.equal(settings.aws_account_id, request.account, 'ACCOUNT_REJECTED');
    assert.equal(request.observed_account, request.account, 'OBSERVED_ACCOUNT_REJECTED');
    assert.equal(settings.aws_region, request.region, 'REGION_REJECTED');
    assert.equal(request.observed_region, request.region, 'OBSERVED_REGION_REJECTED');
    assert.equal(request.cluster_arn, `arn:aws:eks:${request.region}:${request.account}:cluster/tc-oficina-p4-${request.environment_id}`, 'CLUSTER_REJECTED');
    assert.ok(isIPv4(request.observed_ip), 'IP_REJECTED');
    const octets = request.observed_ip.split('.').map(Number);
    assert.ok(octets[0] !== 0 && octets[0] !== 10 && octets[0] !== 127 && octets[0] < 224
        && !(octets[0] === 169 && octets[1] === 254)
        && !(octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31)
        && !(octets[0] === 192 && octets[1] === 168)
        && !(octets[0] === 192 && octets[1] === 0 && [0, 2].includes(octets[2]))
        && !(octets[0] === 192 && octets[1] === 88 && octets[2] === 99)
        && !(octets[0] === 198 && [18, 19].includes(octets[1]))
        && !(octets[0] === 198 && octets[1] === 51 && octets[2] === 100)
        && !(octets[0] === 203 && octets[1] === 0 && octets[2] === 113)
        && !(octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127), 'NON_PUBLIC_IP_REJECTED');
    assert.equal(request.cidr, `${request.observed_ip}/32`, 'CIDR_REJECTED');
    const remaining = Date.parse(request.expires_at) - now.getTime();
    assert.ok(Number.isFinite(remaining) && remaining >= 60_000 && remaining <= 30 * 60_000, 'LEASE_EXPIRY_REJECTED');
    assert.match(request.lease_id, /^[a-z0-9-]{16,64}$/);
    return { request_valid: true, dispatch_allowed: false, reason: 'DURABLE_RECOVERY_NOT_IMPLEMENTED' };
}

function match(pattern, value) {
    const expression = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replaceAll('*', '.*').replaceAll('?', '.');
    return new RegExp(`^${expression}$`, 'i').test(value);
}

export function allowed(policy, action, resource, context = {}) {
    const decisions = policy.Statement.filter(statement => {
        if (![statement.Action].flat().some(pattern => match(pattern, action))) return false;
        if (![statement.Resource].flat().some(pattern => match(pattern, resource))) return false;
        return Object.entries(statement.Condition || {}).every(([operator, conditions]) => Object.entries(conditions).every(([key, expected]) => {
            const actual = context[key];
            if (operator === 'Null') return (actual === undefined) === (expected === 'true');
            if (actual === undefined) return false;
            const choices = [expected].flat().map(String);
            const values = [actual].flat().map(String);
            if (operator === 'ForAllValues:StringEqualsIgnoreCase') return values.every(value => choices.some(choice => value.toLowerCase() === choice.toLowerCase()));
            if (operator === 'ForAnyValue:StringEquals') return values.some(value => choices.includes(value));
            assert.ok(['StringEquals', 'ArnEquals', 'Bool'].includes(operator), 'UNSUPPORTED_TEST_OPERATOR');
            return values.some(value => choices.includes(value));
        }));
    });
    return !decisions.some(statement => statement.Effect === 'Deny') && decisions.some(statement => statement.Effect === 'Allow');
}
