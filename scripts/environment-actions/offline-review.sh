#!/usr/bin/env bash
set -euo pipefail
repository=$(cd "$(dirname "$0")/../.." && pwd)
export AWS_EC2_METADATA_DISABLED=true AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_CONFIG_FILE=/dev/null
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE AWS_WEB_IDENTITY_TOKEN_FILE AWS_ROLE_ARN
bash "$repository/scripts/environment-actions/offline.sh"
for service in auth os-service execution-service billing-service; do
  source="$repository/review-sources/$service"
  test -d "$source" || source="$repository/sources/${service%-service}"
  if test "$service" = auth; then suite="$source/scripts"; else suite="$source/scripts/ci"; fi
  python3 -B -m unittest discover -s "$suite" -p 'test_*.py'
done
