import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const [eventsFile, helm, chartsDirectory, destination] = process.argv.slice(2);
assert.ok(destination, 'Usage: node check-helm.mjs <events.jsonl> <helm> <charts-directory> <destination>');
const events = fs.readFileSync(eventsFile, 'utf8').replace(/^\uFEFF/, '').trim().split(/\r?\n/).map(line => JSON.parse(line));
const outputs = events.find(event => event.type === 'test_plan' && event['@testrun'] === 'render_isolated_platform').test_plan.output_changes;
fs.mkdirSync(destination, { recursive: true });
for (const [name, contract, version] of [
    ['nri-bundle', outputs.observability.after, outputs.observability.after.version],
    ['aws-load-balancer-controller', outputs.network_controller_contract.after, outputs.network_controller_contract.after.chart_version]
]) {
    const values = path.join(destination, `${name}.values.yaml`);
    fs.writeFileSync(values, contract.values_yaml);
    const result = spawnSync(helm, ['template', 'fixture', path.join(chartsDirectory, `${name}-${version}.tgz`),
        '--namespace', contract.namespace, '--kube-version', '1.35.0', '--values', values], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    assert.ok(!/^kind: Ingress$/m.test(result.stdout), 'Controllers must not render edge routing');
    if (name === 'nri-bundle') assert.ok(!/^kind: Secret$/m.test(result.stdout), 'License must come from delivered Secret');
    fs.writeFileSync(path.join(destination, `${name}.rendered.yaml`), result.stdout);
    console.log(`PASS offline Helm ${name} ${version}: Kubernetes 1.35 capabilities; no install performed.`);
}
