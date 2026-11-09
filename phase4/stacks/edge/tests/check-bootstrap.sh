#!/bin/bash
set +x
set -euo pipefail
export SYNTHETIC_BOOTSTRAP_PASSWORD=synthetic_test_value_only_1234567890
output="$(bash /workspace/phase4/stacks/edge/lambda-bootstrap/bin/auth-bootstrap bash -c 'test "$AUTH_DB_PASSWORD" = "$SYNTHETIC_BOOTSTRAP_PASSWORD"; printf ready' 2>&1)"
[[ "$output" == ready ]]
export SYNTHETIC_BOOTSTRAP_FAIL=true
if output="$(bash /workspace/phase4/stacks/edge/lambda-bootstrap/bin/auth-bootstrap bash -c 'printf should-not-run' 2>&1)"; then
  exit 1
fi
[[ "$output" == 'Auth database bootstrap unavailable' ]]
unset SYNTHETIC_BOOTSTRAP_FAIL
export SYNTHETIC_BOOTSTRAP_PASSWORD=short
if output="$(bash /workspace/phase4/stacks/edge/lambda-bootstrap/bin/auth-bootstrap bash -c 'printf should-not-run' 2>&1)"; then
  exit 1
fi
[[ "$output" == 'Auth database bootstrap unavailable' ]]
printf '%s\n' 'Bootstrap wrapper: success, sanitized failure and invalid value verified; no network.'
