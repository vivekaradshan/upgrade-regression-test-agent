# --- Phase 14.5: API Gateway trigger (POST /runs -> start_run Lambda -> Step Functions) ---
#
# HTTP API (not REST API) - cheaper and simpler for a single route with no
# request/response transformation needs. IAM auth (AWS_IAM authorization
# type) reuses the caller's existing AWS credentials instead of minting a
# separate API key to store/rotate alongside GITHUB_TOKEN/OPENAI_API_KEY -
# this is an internal trigger for one operator, not a public API.

resource "aws_iam_role" "start_run" {
  name               = "${var.project_name}-start-run"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "start_run_permissions" {
  source_policy_documents = [data.aws_iam_policy_document.lambda_basic_logging.json]
  statement {
    sid       = "StateStore"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.runs.arn]
  }
  statement {
    sid       = "StartOrchestratorExecution"
    effect    = "Allow"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.orchestrator.arn]
  }
}

resource "aws_iam_role_policy" "start_run" {
  name   = "${var.project_name}-start-run-permissions"
  role   = aws_iam_role.start_run.id
  policy = data.aws_iam_policy_document.start_run_permissions.json
}

resource "aws_lambda_function" "start_run" {
  function_name    = "${var.project_name}-start-run"
  role             = aws_iam_role.start_run.arn
  handler          = "src.aws_lambda.start_run_handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256
  filename         = "${path.module}/../../../build/lambda_placeholder.zip"
  source_code_hash = filebase64sha256("${path.module}/../../../build/lambda_placeholder.zip")
  layers           = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = {
      STATE_MACHINE_ARN = aws_sfn_state_machine.orchestrator.arn
    }
  }
}

resource "aws_apigatewayv2_api" "runs" {
  name          = "${var.project_name}-runs"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "start_run" {
  api_id                 = aws_apigatewayv2_api.runs.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.start_run.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "post_runs" {
  api_id             = aws_apigatewayv2_api.runs.id
  route_key          = "POST /runs"
  target             = "integrations/${aws_apigatewayv2_integration.start_run.id}"
  authorization_type = "AWS_IAM"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.runs.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigateway_invoke_start_run" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.start_run.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.runs.execution_arn}/*/*"
}
