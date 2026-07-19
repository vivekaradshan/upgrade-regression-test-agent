# --- Phase 15.3: human-in-the-loop approval for LLM-diagnosed fixes ---
#
# await_approval is invoked via the state machine's `.waitForTaskToken`
# pattern (state_machine.json.tpl's AwaitApproval state) - it only parks
# the task token in DynamoDB, it doesn't complete the task itself.
# approve_run (POST /runs/{run_id}/approve, same API as start_run) is what
# a human calls - via the dashboard's Approve/Reject button - to actually
# resume the paused execution with their decision.

resource "aws_iam_role" "await_approval" {
  name               = "${var.project_name}-await-approval"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "await_approval_permissions" {
  source_policy_documents = [data.aws_iam_policy_document.lambda_basic_logging.json]
  statement {
    sid       = "StateStore"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.runs.arn]
  }
}

resource "aws_iam_role_policy" "await_approval" {
  name   = "${var.project_name}-await-approval-permissions"
  role   = aws_iam_role.await_approval.id
  policy = data.aws_iam_policy_document.await_approval_permissions.json
}

resource "aws_lambda_function" "await_approval" {
  function_name    = "${var.project_name}-await-approval"
  role             = aws_iam_role.await_approval.arn
  handler          = "src.aws_lambda.await_approval_handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256
  filename         = "${path.module}/../../../build/lambda_placeholder.zip"
  source_code_hash = filebase64sha256("${path.module}/../../../build/lambda_placeholder.zip")
  layers           = [aws_lambda_layer_version.shared.arn]
}

# ---- approve_run ----

resource "aws_iam_role" "approve_run" {
  name               = "${var.project_name}-approve-run"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "approve_run_permissions" {
  source_policy_documents = [data.aws_iam_policy_document.lambda_basic_logging.json]
  statement {
    sid       = "GithubTokenSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.github_token.arn]
  }
  statement {
    sid       = "StateStore"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.runs.arn]
  }
  statement {
    # SendTaskSuccess/SendTaskFailure aren't resource-scopable (the task
    # token isn't an ARN) - "*" is the AWS-documented pattern for these
    # two actions specifically, not a broad grant of other states:*
    # actions.
    sid       = "ResumeApprovalTask"
    effect    = "Allow"
    actions   = ["states:SendTaskSuccess", "states:SendTaskFailure"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "approve_run" {
  name   = "${var.project_name}-approve-run-permissions"
  role   = aws_iam_role.approve_run.id
  policy = data.aws_iam_policy_document.approve_run_permissions.json
}

resource "aws_lambda_function" "approve_run" {
  function_name    = "${var.project_name}-approve-run"
  role             = aws_iam_role.approve_run.arn
  handler          = "src.aws_lambda.approve_run_handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256
  filename         = "${path.module}/../../../build/lambda_placeholder.zip"
  source_code_hash = filebase64sha256("${path.module}/../../../build/lambda_placeholder.zip")
  layers           = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = {
      GITHUB_TOKEN_SECRET_ID = aws_secretsmanager_secret.github_token.name
    }
  }
}

# Reuses the same HTTP API as start_run (api_gateway.tf) rather than a
# second API - one operator-facing API surface for triggering and
# approving runs.
resource "aws_apigatewayv2_integration" "approve_run" {
  api_id                 = aws_apigatewayv2_api.runs.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.approve_run.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "post_runs_approve" {
  api_id             = aws_apigatewayv2_api.runs.id
  route_key          = "POST /runs/{run_id}/approve"
  target             = "integrations/${aws_apigatewayv2_integration.approve_run.id}"
  authorization_type = "AWS_IAM"
}

resource "aws_lambda_permission" "apigateway_invoke_approve_run" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.approve_run.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.runs.execution_arn}/*/*"
}
