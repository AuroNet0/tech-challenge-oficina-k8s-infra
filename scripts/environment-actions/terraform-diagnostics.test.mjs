import test from 'node:test';
import assert from 'node:assert/strict';
import { command } from './diagnostics.mjs';
import { sanitizeTerraformDiagnostic, terraformDiagnostics } from './terraform-diagnostics.mjs';

test('diagnostic sanitization removes credential formats and multiline source values', () => {
  const output = sanitizeTerraformDiagnostic([
    'AKIA1234567890ABCDEF', 'ASIA1234567890ABCDEF',
    'eyJhbGciOiJIUzI1NiJ9.payload.signature',
    '-----BEGIN PRIVATE KEY-----\nkey-fixture\n-----END PRIVATE KEY-----',
    'password="multiline-fixture\nsecond-line-fixture"',
    'postgres://dbuser:database-fixture@database.test/db',
    'https://provider.test/download?X-Amz-Signature=signed-fixture&other=value',
    '  14: example = "source-value-fixture"',
    'Error: Failed to query available provider packages',
  ].join('\n'));
  assert.doesNotMatch(output, /AKIA|ASIA|eyJ|fixture|PRIVATE KEY/);
  assert.match(output, /Failed to query available provider packages/);
});

test('invalid or missing validate JSON uses sanitized stderr without publishing raw stdout', () => {
  for (const stdout of ['broken-json-private-fixture', '{}', '{"diagnostics":null}']) {
    const result = command('terraform', ['validate', '-json'], () => ({ status: 1, stdout,
      stderr: 'Error: Failed to load plugin schemas password=stderr-fixture' }));
    assert.equal(result.status, 1);
    assert.match(JSON.stringify(result.diagnostics), /Failed to load plugin schemas/);
    assert.doesNotMatch(JSON.stringify(result.diagnostics), /broken-json-private-fixture|stderr-fixture/);
  }
  assert.match(terraformDiagnostics('broken-json-private-fixture', '', true)[0].detail, /No usable diagnostics/);
});

test('plain Terraform stdout retains only the error section and sanitized source excerpts', () => {
  const result = command('terraform', ['plan'], () => ({ status: 3,
    stdout: 'Plan output private-fixture\nError: Invalid provider configuration\n  3: value = "source-fixture"', stderr: '' }));
  assert.equal(result.status, 3);
  assert.match(result.diagnostics[0].detail, /Invalid provider configuration/);
  assert.doesNotMatch(result.diagnostics[0].detail, /private-fixture|source-fixture/);
});
