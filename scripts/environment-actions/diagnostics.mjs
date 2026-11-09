import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { Report, errorCode } from './control.mjs';

export function command(executable, argumentsList, execute = spawnSync) {
  const started = Date.now();
  const result = execute(executable, argumentsList, { encoding: 'utf8', timeout: executable === 'terraform' ? 600000 : 60000, maxBuffer: 8 * 1024 * 1024 });
  return { status: result.error ? 1 : result.status ?? 1, duration_ms: Date.now() - started,
    reason: result.status === 0 && !result.error ? 'VERIFIED'
      : result.error?.code === 'ENOENT' ? 'EXECUTABLE_UNAVAILABLE' : errorCode(result.stderr), stdout: result.stdout || '' };
}

export async function terraformValidation(directory, report, execute = spawnSync) {
  for (const argumentsList of [['fmt', '-check', '-recursive'], ['init', '-backend=false', '-input=false', '-lockfile=readonly'], ['validate', '-json']]) {
    const success = await report.check(`terraform-${argumentsList[0]}`, path.basename(directory), () => {
      const result = command('terraform', [`-chdir=${directory}`, ...argumentsList], execute);
      if (result.status !== 0) throw new Error(result.reason);
    }, 'Review the Terraform configuration, provider lock and backend diagnostic code. Raw output is withheld.');
    if (!success) return false;
  }
  return true;
}

export function summarizeKubernetes(document) {
  return (document.items || []).map(item => ({
    kind: item.kind,
    name: item.metadata?.name,
    namespace: item.metadata?.namespace,
    phase: item.status?.phase,
    ready_replicas: item.status?.readyReplicas,
    succeeded: item.status?.succeeded,
    failed: item.status?.failed,
    conditions: (item.status?.conditions || []).map(condition => ({ type: condition.type, status: condition.status,
      reason: /^[A-Za-z0-9_]{1,100}$/.test(condition.reason || '') ? condition.reason : undefined })),
    containers: [...(item.status?.initContainerStatuses || []), ...(item.status?.containerStatuses || [])].map(container => ({ name: container.name,
      ready: container.ready, restart_count: container.restartCount,
      reason: errorCode(JSON.stringify(container.state)),
      exit_code: container.state?.terminated?.exitCode })),
  }));
}

export function logSignatures(output) {
  const signatures = ['CrashLoopBackOff', 'ImagePullBackOff', 'OutOfMemoryError', 'OOMKilled',
    'Connection refused', 'Connection timed out', 'FlywayException', 'PSQLException',
    'MongoTimeoutException', 'AmqpConnectException', 'SSLHandshakeException', 'APPLICATION FAILED TO START'];
  return { raw_content_withheld: true, signatures: signatures.filter(signature => output.includes(signature)) };
}

export async function collectKubernetes(namespace, report, execute = spawnSync) {
  if (namespace !== 'tc-oficina-p4-demo-a') throw new Error('NAMESPACE_REJECTED');
  const observations = [];
  const failedPods = [];
  for (const [resource, argumentsList] of [
    ['nodes', ['get', 'nodes', '-o', 'json']],
    ['workloads', ['get', 'pods,deployments,jobs,statefulsets,pvc', '-n', namespace, '-o', 'json']],
  ]) {
    await report.check('kubernetes-diagnostic', resource, () => {
      const result = command('kubectl', [...argumentsList, '--request-timeout=15s'], execute);
      if (result.status !== 0) throw new Error(result.reason);
      const document = JSON.parse(result.stdout);
      observations.push(...summarizeKubernetes(document));
      failedPods.push(...(document.items || []).filter(item => item.kind === 'Pod'
        && (item.status?.phase === 'Failed' || [...(item.status?.initContainerStatuses || []), ...(item.status?.containerStatuses || [])]
          .some(container => container.state?.waiting || container.state?.terminated?.exitCode > 0))));
    }, 'Check private endpoint connectivity, RBAC, scheduling, probes, image pulls and migration job status.');
  }
  await report.check('kubernetes-diagnostic', 'recent event reasons', () => {
    const result = command('kubectl', ['get', 'events', '-n', namespace, '-o', 'json', '--request-timeout=15s'], execute);
    if (result.status !== 0) throw new Error(result.reason);
    observations.push({ kind: 'EventSummary', reasons: (JSON.parse(result.stdout).items || []).slice(-50)
      .map(event => ({ reason: /^[A-Za-z0-9_]{1,100}$/.test(event.reason || '') ? event.reason : 'WITHHELD', count: event.count })) });
  });
  for (const pod of failedPods.slice(0, 10)) {
    const name = pod.metadata?.name;
    if (!/^[a-z0-9][a-z0-9.-]{0,252}$/.test(name || '')) continue;
    for (const previous of [false, true]) {
      await report.check('container-log-signatures', name, () => {
        const argumentsList = ['logs', name, '-n', namespace, '--all-containers=true', '--tail=80',
          '--limit-bytes=16384', '--request-timeout=15s', ...(previous ? ['--previous'] : [])];
        const result = command('kubectl', argumentsList, execute);
        if (result.status !== 0) throw new Error(result.reason);
        observations.push({ kind: 'ContainerLogSignatures', name, previous, ...logSignatures(result.stdout) });
      }, 'Inspect application startup, migration, TLS and dependency connectivity using the reported signatures. Raw log content is withheld.');
    }
  }
  fs.writeFileSync(path.join(report.directory, 'kubernetes.json'), JSON.stringify(observations, null, 2));
}

async function main() {
  const directory = process.env.REPORT_DIR || 'actions-report';
  if (process.argv[2] === 'fallback') {
    if (!fs.existsSync(path.join(directory, 'report.json'))) {
      const report = new Report(process.argv[3], directory);
      await report.check('workflow-setup', 'checkout, approval or authentication', () => { throw new Error('WORKFLOW_SETUP_FAILED'); },
        'Inspect the failed GitHub step; verify dispatch branch, read-only approval, environment protection and OIDC bootstrap.');
      report.finish();
    }
    return;
  }
  if (process.argv[2] === 'validate') {
    const report = new Report('offline-terraform', directory);
    for (const stack of ['infra', 'platform', 'edge']) await terraformValidation(`phase4/stacks/${stack}`, report);
    process.exitCode = report.finish();
    return;
  }
  throw new Error('OPERATION_REJECTED');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
