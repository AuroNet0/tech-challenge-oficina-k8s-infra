import fs from 'node:fs';
import { validateInputs, reviewPlan } from './bootstrap.mjs';

const inputs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
validateInputs(inputs);
if (process.argv[3]) {
  reviewPlan(JSON.parse(fs.readFileSync(process.argv[3], 'utf8')), process.env.GITHUB_SHA,
    inputs.artifact_bucket_installation_approved === true, inputs.recovery_installation_approved === true);
}
