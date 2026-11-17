#!/usr/bin/env bash
# Create AgentCore Identity credential providers for configured Git hosts.
set -euo pipefail

APP_NAME="${APP_NAME:-rca}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"

echo "Creating credential providers in $REGION for prefix $APP_NAME"
echo "Implement provider secrets in Secrets Manager before running tasks."