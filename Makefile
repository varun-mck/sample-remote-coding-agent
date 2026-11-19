PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
CDK_VERSION ?= 2.1149.0
CDK = npx --yes aws-cdk@$(CDK_VERSION)
STACK_PREFIX ?= SampleCodingAgent
RCA_HOME ?= $(HOME)/.rca
OUTPUTS ?= $(RCA_HOME)/stack-outputs.json
ACCOUNT_WAIT_SECONDS ?= 900
ACCOUNT_WAIT_INTERVAL ?= 15

API_STACK = $(STACK_PREFIX)Api
AGENT_STACK = $(STACK_PREFIX)Runtime
GATEWAY_STACK = $(STACK_PREFIX)Gateway
ACCOUNT_STACK = $(STACK_PREFIX)Account
DATA_STACK = $(STACK_PREFIX)Data

.PHONY: setup test lint preflight deploy budget-reset task task-get task-list

setup:
	test -x .venv/bin/pip || python3 -m venv --system-site-packages "$$(cd .venv 2>/dev/null && pwd -P || echo .venv)"
	.venv/bin/python -m pip install --quiet --no-cache-dir --upgrade pip
	.venv/bin/python -m pip install --quiet --no-cache-dir -r requirements-dev.txt
	.venv/bin/python -m pip install --quiet --no-cache-dir -e ./agent
	.venv/bin/python -m pip install --quiet --no-cache-dir -e ./kiro-rca-power
	.venv/bin/python -m pip install --quiet --no-cache-dir -r api/requirements.txt -r infra/requirements.txt

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check agent/src api/src infra tests scripts kiro-rca-power/src
	$(PYTHON) -m ruff format --check agent/src api/src infra tests scripts kiro-rca-power/src
	$(PYTHON) -m mypy --config-file mypy.ini agent/src api/src infra kiro-rca-power/src

preflight:
	@test -x .venv/bin/python || { echo "run make setup first"; exit 1; }
	@$(PYTHON) -m ruff check agent/src api/src infra tests scripts
	@$(PYTHON) -c "import aws_cdk" || { echo "infra deps missing"; exit 1; }
	@command -v docker >/dev/null || { echo "docker required for agent image build"; exit 1; }
	@uname -m | grep -q arm64 || echo "warning: agent image is ARM64; deploy from arm64 or buildx"

deploy:
	@test -n "$(EMAIL)" || { echo "EMAIL required for Bedrock budget alerts: make deploy EMAIL=you@example.com"; exit 1; }
	@$(MAKE) --no-print-directory preflight
	@mkdir -p "$(RCA_HOME)"
	@set -eu; \
	account=$$(aws sts get-caller-identity --query Account --output text); \
	region=$${AWS_REGION:-$${AWS_DEFAULT_REGION:-$$(aws configure get region)}}; \
	cd infra && PATH="$(CURDIR)/.venv/bin:$$PATH" $(CDK) deploy --all \
	  --require-approval never --outputs-file "$(OUTPUTS)" \
	  -c account="$$account" -c region="$$region" \
	  -c stackPrefix=$(STACK_PREFIX) -c budgetAlertEmail=$(EMAIL)
	@set -eu; \
	api=$$($(PYTHON) -c "import json,sys; o=json.load(open('$(OUTPUTS)')); print(o['$(API_STACK)']['ApiUrl'])"); \
	region=$${AWS_REGION:-$${AWS_DEFAULT_REGION:-$$(aws configure get region)}}; \
	printf '%s\n' "{\"apiUrl\":\"$$api\",\"stackName\":\"$(API_STACK)\",\"region\":\"$$region\",\"writtenBy\":\"make deploy\"}" > "$(RCA_HOME)/endpoint.json"; \
	echo "api: $$api"; \
	echo "wrote $(RCA_HOME)/endpoint.json"

budget-reset:
	@set -eu; \
	region=$${AWS_REGION:-$${AWS_DEFAULT_REGION:-$$(aws configure get region)}}; \
	stack=$$(PYTHONPATH=infra $(PYTHON) -c 'import app,sys; print(app.stack_names(sys.argv[1],sys.argv[2]).account)' "$(STACK_PREFIX)" "$$region"); \
	outputs=$$(aws cloudformation describe-stacks --stack-name "$$stack" --region us-east-1 --query Stacks[0].Outputs --output json); \
	policy_arn=$$(printf '%s' "$$outputs" | $(PYTHON) -c 'import json,sys; outs={o["OutputKey"]:o["OutputValue"] for o in json.load(sys.stdin)}; print(outs["SpendCapPolicyArn"])'); \
	for key in ExecutionRoleArn DispatcherRoleArn; do \
	  role_arn=$$(printf '%s' "$$outputs" | $(PYTHON) -c 'import json,sys; k=sys.argv[1]; outs={o["OutputKey"]:o["OutputValue"] for o in json.load(sys.stdin)}; print(outs[k])' "$$key"); \
	  aws iam detach-role-policy --role-name "$$(basename "$$role_arn")" --policy-arn "$$policy_arn" || true; \
	done

task:
	@test -n "$(REPO)" -a -n "$(BRANCH)" -a -n "$(PROMPT)" || \
	  { echo "usage: make task REPO=https://github.com/o/r BRANCH=feat/x PROMPT='...' [MODEL=id] [EFFORT=low|medium|high|xhigh|max]"; exit 1; }
	@$(PYTHON) scripts/rca_cli.py submit --repo "$(REPO)" --branch "$(BRANCH)" --prompt "$(PROMPT)" \
	  $(if $(MODEL),--model "$(MODEL)") $(if $(EFFORT),--effort "$(EFFORT)")

task-get:
	@test -n "$(ID)" || { echo "usage: make task-get ID=tsk_..."; exit 1; }
	@$(PYTHON) scripts/rca_cli.py get "$(ID)"

task-list:
	@$(PYTHON) scripts/rca_cli.py list