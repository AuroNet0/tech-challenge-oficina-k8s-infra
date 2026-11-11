#!/usr/bin/env bash
set -euo pipefail
assets=${1:?Supply the local Terraform tool directory with bin/terraform and corrected-data/providers}
repository=$(cd "$(dirname "$0")/../.." && pwd)
review=${OFFLINE_REVIEW_DIR:-$(mktemp -d /tmp/pipeline-offline.XXXXXX)}
mkdir -p "$review/tree" "$review/logs"
cp -R "$repository/phase4" "$review/tree/phase4"
printf 'provider_installation { filesystem_mirror { path = "%s" } }\n' "$assets/corrected-data/providers" > "$review/offline.tfrc"
export TF_CLI_CONFIG_FILE="$review/offline.tfrc" AWS_EC2_METADATA_DISABLED=true CHECKPOINT_DISABLE=1 TF_IN_AUTOMATION=true TF_INPUT=false TF_WORKSPACE=default
export AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_CONFIG_FILE=/dev/null
unset TF_PLUGIN_CACHE_DIR TF_PLUGIN_CACHE_MAY_BREAK_DEPENDENCY_LOCK_FILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE AWS_WEB_IDENTITY_TOKEN_FILE AWS_ROLE_ARN TF_CLI_ARGS TF_CLI_ARGS_init TF_CLI_ARGS_plan TF_CLI_ARGS_test
python3 - "$repository/scripts/eks-endpoint-recovery/recovery.py" "$review/recovery.zip" <<'PY'
import sys
import zipfile
with zipfile.ZipFile(sys.argv[2], 'w') as archive:
    archive.write(sys.argv[1], 'recovery.py')
PY
"$assets/bin/terraform" -chdir="$review/tree/phase4" fmt -check -recursive -diff
for root in modules/environment stacks/infra stacks/platform stacks/edge bootstrap/identities modules/endpoint-recovery; do
  label=${root//\//-}
  directory="$review/tree/phase4/$root"
  export TF_DATA_DIR="$review/data/$label"
  if test "$root" = modules/endpoint-recovery; then cp "$review/tree/phase4/bootstrap/identities/.terraform.lock.hcl" "$directory/.terraform.lock.hcl"; fi
  extra=()
  if test "$root" = modules/endpoint-recovery; then extra=("-var=package_path=$review/recovery.zip"); fi
  "$assets/bin/terraform" -chdir="$directory" init -backend=false -input=false -lockfile=readonly -no-color > "$review/logs/$label-init.log" 2>&1 || { cat "$review/logs/$label-init.log"; exit 1; }
  "$assets/bin/terraform" -chdir="$directory" validate -no-color
  "$assets/bin/terraform" -chdir="$directory" test -no-color "${extra[@]}" > "$review/logs/$label-test.log" 2>&1 || { cat "$review/logs/$label-test.log"; exit 1; }
  tail -n 1 "$review/logs/$label-test.log"
done
export TF_DATA_DIR="$review/data/stacks-platform"
"$assets/bin/terraform" -chdir="$review/tree/phase4/stacks/platform" test -filter=tests/platform.tftest.hcl -json -verbose > "$review/logs/platform-contract.jsonl"
if command -v node >/dev/null; then
  node "$repository/phase4/stacks/platform/tests/check-delivery-contract.mjs" "$review/logs/platform-contract.jsonl" "$review/public-contract.json"
else
  printf 'Public fixture requires Node: phase4/stacks/platform/tests/check-delivery-contract.mjs %s %s\n' "$review/logs/platform-contract.jsonl" "$review/public-contract.json"
fi
printf 'OFFLINE_RESULT_DIR=%s\n' "$review"
