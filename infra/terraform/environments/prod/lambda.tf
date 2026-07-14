# --- Phase 14.3: Lambda handlers wrapping orchestrator nodes ---

# Extra EMR job dependencies not already covered by Phase 14.2's pyyaml
# upload: validate_job.py itself, and the small src.tools.data_validator
# package it imports (see infra/scripts/build_validator_pyfiles.sh).
resource "aws_s3_object" "validate_job_driver" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "drivers/validate_job.py"
  source = "${path.module}/../../../spark_drivers/validate_job.py"
  etag   = filemd5("${path.module}/../../../spark_drivers/validate_job.py")
}

resource "aws_s3_object" "validator_pyfiles" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "dependencies/validator_deps.zip"
  source = "${path.module}/../../../build/validator_deps.zip"
  etag   = filemd5("${path.module}/../../../build/validator_deps.zip")
}

# All 6 handlers' code lives entirely in this layer (src/ copied in whole,
# including src/aws_lambda/*.py itself) - Lambda extracts layers to
# /opt/python, which is automatically on PYTHONPATH for Python runtimes,
# so each function's own deployment package can be a trivial placeholder.
# Run infra/scripts/build_lambda_layer.sh to (re)generate this file before
# applying - Terraform uploads whatever's currently built, same pattern as
# the pyyaml/validator py-files packages.
resource "aws_lambda_layer_version" "shared" {
  layer_name          = "${var.project_name}-shared"
  filename            = "${path.module}/../../../build/lambda_layer.zip"
  source_code_hash    = filebase64sha256("${path.module}/../../../build/lambda_layer.zip")
  compatible_runtimes = ["python3.12"]
}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "lambda_basic_logging" {
  statement {
    sid     = "Logging"
    effect  = "Allow"
    actions = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/*"]
  }
}

locals {
  lambda_common_env = {
    GITHUB_TOKEN_SECRET_ID   = aws_secretsmanager_secret.github_token.name
    OPENAI_API_KEY_SECRET_ID = aws_secretsmanager_secret.openai_api_key.name
    ARTIFACTS_BUCKET         = aws_s3_bucket.artifacts.id
    REPORTS_BUCKET           = aws_s3_bucket.reports.id
    BASELINE_APPLICATION_ID  = aws_emrserverless_application.baseline.id
    TARGET_APPLICATION_ID    = aws_emrserverless_application.target.id
    EMR_EXECUTION_ROLE_ARN   = aws_iam_role.emr_serverless_execution.arn
    PYYAML_PYFILES_S3_URI    = "s3://${aws_s3_bucket.artifacts.id}/${aws_s3_object.pyyaml_pyfiles.key}"
    VALIDATOR_PYFILES_S3_URI = "s3://${aws_s3_bucket.artifacts.id}/${aws_s3_object.validator_pyfiles.key}"
  }
}

# ---- create_branches ----

resource "aws_iam_role" "create_branches" {
  name               = "${var.project_name}-create-branches"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "create_branches_permissions" {
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
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.runs.arn]
  }
}

resource "aws_iam_role_policy" "create_branches" {
  name   = "${var.project_name}-create-branches-permissions"
  role   = aws_iam_role.create_branches.id
  policy = data.aws_iam_policy_document.create_branches_permissions.json
}

resource "aws_lambda_function" "create_branches" {
  function_name    = "${var.project_name}-create-branches"
  role             = aws_iam_role.create_branches.arn
  handler          = "src.aws_lambda.create_branches_handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = "${path.module}/../../../build/lambda_placeholder.zip"
  source_code_hash = filebase64sha256("${path.module}/../../../build/lambda_placeholder.zip")
  layers           = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = local.lambda_common_env
  }
}

# ---- mock_build ----

resource "aws_iam_role" "mock_build" {
  name               = "${var.project_name}-mock-build"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "mock_build_permissions" {
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
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.runs.arn]
  }
}

resource "aws_iam_role_policy" "mock_build" {
  name   = "${var.project_name}-mock-build-permissions"
  role   = aws_iam_role.mock_build.id
  policy = data.aws_iam_policy_document.mock_build_permissions.json
}

resource "aws_lambda_function" "mock_build" {
  function_name    = "${var.project_name}-mock-build"
  role             = aws_iam_role.mock_build.arn
  handler          = "src.aws_lambda.mock_build_handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256
  filename         = "${path.module}/../../../build/lambda_placeholder.zip"
  source_code_hash = filebase64sha256("${path.module}/../../../build/lambda_placeholder.zip")
  layers           = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = local.lambda_common_env
  }
}

# ---- prepare_execution ----
# No StateStore/DynamoDB access needed - this handler only fetches GitHub
# content and writes EMR job inputs to S3, confirmed by reading its code.

resource "aws_iam_role" "prepare_execution" {
  name               = "${var.project_name}-prepare-execution"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "prepare_execution_permissions" {
  source_policy_documents = [data.aws_iam_policy_document.lambda_basic_logging.json]
  statement {
    sid       = "GithubTokenSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.github_token.arn]
  }
  statement {
    sid       = "ArtifactsBucket"
    effect    = "Allow"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:HeadObject"]
    resources = ["${aws_s3_bucket.artifacts.arn}/*"]
  }
}

resource "aws_iam_role_policy" "prepare_execution" {
  name   = "${var.project_name}-prepare-execution-permissions"
  role   = aws_iam_role.prepare_execution.id
  policy = data.aws_iam_policy_document.prepare_execution_permissions.json
}

resource "aws_lambda_function" "prepare_execution" {
  function_name    = "${var.project_name}-prepare-execution"
  role             = aws_iam_role.prepare_execution.arn
  handler          = "src.aws_lambda.prepare_execution_handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = "${path.module}/../../../build/lambda_placeholder.zip"
  source_code_hash = filebase64sha256("${path.module}/../../../build/lambda_placeholder.zip")
  layers           = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = local.lambda_common_env
  }
}

# ---- analyze_logs ----

resource "aws_iam_role" "analyze_logs" {
  name               = "${var.project_name}-analyze-logs"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "analyze_logs_permissions" {
  source_policy_documents = [data.aws_iam_policy_document.lambda_basic_logging.json]
  statement {
    sid    = "SecretsAccess"
    effect = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.github_token.arn,
      aws_secretsmanager_secret.openai_api_key.arn,
    ]
  }
  statement {
    sid       = "StateStore"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.runs.arn]
  }
  statement {
    sid       = "ReadJobLogs"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.artifacts.arn}/*"]
  }
}

resource "aws_iam_role_policy" "analyze_logs" {
  name   = "${var.project_name}-analyze-logs-permissions"
  role   = aws_iam_role.analyze_logs.id
  policy = data.aws_iam_policy_document.analyze_logs_permissions.json
}

resource "aws_lambda_function" "analyze_logs" {
  function_name    = "${var.project_name}-analyze-logs"
  role             = aws_iam_role.analyze_logs.arn
  handler          = "src.aws_lambda.analyze_logs_handler.handler"
  runtime          = "python3.12"
  timeout          = 120
  memory_size      = 512
  filename         = "${path.module}/../../../build/lambda_placeholder.zip"
  source_code_hash = filebase64sha256("${path.module}/../../../build/lambda_placeholder.zip")
  layers           = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = local.lambda_common_env
  }
}

# ---- generate_report ----
# No GitHub access needed - report_generator.py only reads run state and
# writes to S3, confirmed by reading its code.

resource "aws_iam_role" "generate_report" {
  name               = "${var.project_name}-generate-report"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "generate_report_permissions" {
  source_policy_documents = [data.aws_iam_policy_document.lambda_basic_logging.json]
  statement {
    sid       = "StateStore"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.runs.arn]
  }
  statement {
    sid       = "WriteReports"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.reports.arn}/*"]
  }
}

resource "aws_iam_role_policy" "generate_report" {
  name   = "${var.project_name}-generate-report-permissions"
  role   = aws_iam_role.generate_report.id
  policy = data.aws_iam_policy_document.generate_report_permissions.json
}

resource "aws_lambda_function" "generate_report" {
  function_name    = "${var.project_name}-generate-report"
  role             = aws_iam_role.generate_report.arn
  handler          = "src.aws_lambda.generate_report_handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256
  filename         = "${path.module}/../../../build/lambda_placeholder.zip"
  source_code_hash = filebase64sha256("${path.module}/../../../build/lambda_placeholder.zip")
  layers           = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = local.lambda_common_env
  }
}

# ---- raise_pr ----

resource "aws_iam_role" "raise_pr" {
  name               = "${var.project_name}-raise-pr"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "raise_pr_permissions" {
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
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.runs.arn]
  }
}

resource "aws_iam_role_policy" "raise_pr" {
  name   = "${var.project_name}-raise-pr-permissions"
  role   = aws_iam_role.raise_pr.id
  policy = data.aws_iam_policy_document.raise_pr_permissions.json
}

resource "aws_lambda_function" "raise_pr" {
  function_name    = "${var.project_name}-raise-pr"
  role             = aws_iam_role.raise_pr.arn
  handler          = "src.aws_lambda.raise_pr_handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256
  filename         = "${path.module}/../../../build/lambda_placeholder.zip"
  source_code_hash = filebase64sha256("${path.module}/../../../build/lambda_placeholder.zip")
  layers           = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = local.lambda_common_env
  }
}
