#!/usr/bin/env bash
set -euo pipefail
repository=$(cd "$(dirname "$0")/../.." && pwd)
for suite in environment-actions eks-endpoint-recovery artifact-release platform-secrets; do
  python3 -B -m unittest discover -s "$repository/scripts/$suite" -p 'test_*.py'
done
