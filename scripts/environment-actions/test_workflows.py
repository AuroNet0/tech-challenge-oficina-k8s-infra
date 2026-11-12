from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]


class UniqueLoader(yaml.BaseLoader):
    def construct_mapping(self, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise ValueError('Duplicate YAML key: ' + key)
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


class WorkflowTests(unittest.TestCase):
    def test_only_three_manual_workflows_and_every_job_publishes_diagnostics_after_failure(self):
        files = sorted(path for path in (ROOT / '.github/workflows').iterdir()
                       if path.suffix in ('.yml', '.yaml'))
        self.assertEqual([path.name for path in files], ['deploy.yml', 'destroy.yml', 'plan.yml'])
        for path in files:
            document = yaml.load(path.read_text(), Loader=UniqueLoader)
            self.assertEqual(set(document['on']), {'workflow_dispatch'})
            self.assertEqual(document['concurrency']['group'], 'phase4-demo-a-mutation')
            self.assertEqual(document['concurrency']['cancel-in-progress'], 'false')
            for job in document['jobs'].values():
                steps = job['steps']
                summaries = [step for step in steps if any(script in step.get('run', '')
                    for script in ('finalize.py', 'complete_report.py'))]
                uploads = [step for step in steps if step.get('uses') == 'actions/upload-artifact@v4'
                           and 'diagnostics-' in step['with']['name']]
                self.assertEqual(len(summaries), 1)
                self.assertEqual(len(uploads), 1)
                for step in summaries + uploads:
                    self.assertEqual(step['if'], '${{ always() }}')
                self.assertFalse(any(step.get('continue-on-error') == 'true' for step in steps))
                if path.name in ('deploy.yml', 'destroy.yml') and 'id-token' in job.get('permissions', {}):
                    self.assertTrue(job.get('environment'))
                    self.assertIn('PROTECTED_ENVIRONMENT', job['env'])

    def test_composite_setup_has_no_mutations_and_yaml_has_unique_keys(self):
        document = yaml.load((ROOT / '.github/actions/setup/action.yml').read_text(), Loader=UniqueLoader)
        self.assertEqual(document['runs']['using'], 'composite')
        self.assertFalse(any('terraform apply' in step.get('run', '') for step in document['runs']['steps']))

    def test_complete_dispatch_has_no_phase_and_mutations_use_one_approved_job(self):
        from complete import BLOCKS
        plan = yaml.load((ROOT / '.github/workflows/plan.yml').read_text(), Loader=UniqueLoader)
        deploy = yaml.load((ROOT / '.github/workflows/deploy.yml').read_text(), Loader=UniqueLoader)
        self.assertFalse(plan['on']['workflow_dispatch'])
        self.assertEqual(set(deploy['on']['workflow_dispatch']['inputs']), {'plan_run', 'approved_plan_sha256'})
        mutating = [job for job in deploy['jobs'].values() if job.get('permissions', {}).get('id-token') == 'write']
        self.assertEqual(len(mutating), 1)
        self.assertEqual(mutating[0]['environment'], 'phase4-demo-a-bootstrap')
        action = next(step for step in mutating[0]['steps'] if step.get('uses') == './.github/actions/complete')
        self.assertEqual(action['with']['operation'], 'deploy-all')
        destroy = yaml.load((ROOT / '.github/workflows/destroy.yml').read_text(), Loader=UniqueLoader)
        mutating = [job for job in destroy['jobs'].values() if job.get('permissions', {}).get('id-token') == 'write']
        self.assertEqual(len(mutating), 1)
        self.assertEqual(mutating[0]['environment'], 'phase4-demo-a-destroy')
        document = yaml.load((ROOT / '.github/actions/complete/action.yml').read_text(), Loader=UniqueLoader)
        self.assertEqual(document['runs']['using'], 'composite')


if __name__ == '__main__':
    unittest.main()
