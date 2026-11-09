#!/bin/bash
set +x
set -euo pipefail
export SYNTHETIC_BOOTSTRAP_PASSWORD=synthetic_fixture_value_only_1234567890
wrapper=${WRAPPER_PATH:-/opt/bin/auth-bootstrap}
test -x "$wrapper"
test -r /opt/auth-home/.postgresql/root.crt
output="$(bash "$wrapper" bash -c 'test "$AUTH_DB_PASSWORD" = "$SYNTHETIC_BOOTSTRAP_PASSWORD"; test "$1" = "runtime argument"; printf ready' runtime 'runtime argument' 2>&1)"
[[ "$output" == ready ]]
for mode in missing invalid wrong-user; do
  export SYNTHETIC_BOOTSTRAP_FAIL=true
  if output="$(bash "$wrapper" bash -c 'printf should-not-run' 2>&1)"; then exit 1; fi
  [[ "$output" == 'Auth database bootstrap unavailable' ]]
done
unset SYNTHETIC_BOOTSTRAP_FAIL
export SYNTHETIC_BOOTSTRAP_PASSWORD=short
if output="$(bash "$wrapper" bash -c 'printf should-not-run' 2>&1)"; then exit 1; fi
[[ "$output" == 'Auth database bootstrap unavailable' ]]
export SYNTHETIC_BOOTSTRAP_PASSWORD=synthetic_fixture_value_only_1234567890
bash "$wrapper" bash -c 'exit 23' >/tmp/bootstrap-contract.out 2>&1 && exit 1 || [[ "$?" == 23 ]]
[[ ! -s /tmp/bootstrap-contract.out ]]
output="$(bash -c 'expected=$$; export EXPECTED_PID=$expected; exec bash "$1" bash -c '\''test "$$" = "$EXPECTED_PID"; printf exec-ok'\''' runtime "$wrapper")"
[[ "$output" == exec-ok ]]
if output="$(env -u AUTH_DB_SECRET_ID java -cp /opt/java/lib/auth-bootstrap.jar oficina.edge.AuthDatabaseBootstrap 2>&1)"; then exit 1; fi
[[ -z "$output" ]]
if output="$(AUTH_DB_SECRET_ID=/oficina/phase4/demo-a/auth-postgres AUTH_ENVIRONMENT=demo-a AUTH_DB_USER=foreign AWS_REGION=us-east-1 java -cp /opt/java/lib/auth-bootstrap.jar oficina.edge.AuthDatabaseBootstrap 2>&1)"; then exit 1; fi
[[ -z "$output" ]]
bash "$wrapper" bash -c 'trap "exit 42" TERM; : > /tmp/bootstrap-termination-ready; while true; do sleep 0.05; done' &
runtime_pid=$!
for attempt in {1..100}; do
  [[ -f /tmp/bootstrap-termination-ready ]] && break
  sleep 0.05
done
test -f /tmp/bootstrap-termination-ready
kill -TERM "$runtime_pid"
if wait "$runtime_pid"; then exit 1; else [[ "$?" == 42 ]]; fi
printf '%s\n' 'Bootstrap wrapper: arguments, exec, exit status and sanitized failures passed offline.'
