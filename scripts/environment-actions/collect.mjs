import { Report } from './control.mjs';
import { collectKubernetes } from './diagnostics.mjs';

const report = new Report('failure-diagnostics', 'actions-report/kubernetes-diagnostic');
try {
  await collectKubernetes('tc-oficina-p4-demo-a', report);
} finally {
  report.finish();
}
