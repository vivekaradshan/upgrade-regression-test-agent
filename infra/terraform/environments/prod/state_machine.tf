data "aws_iam_policy_document" "step_functions_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "step_functions_execution" {
  name               = "${var.project_name}-step-functions-execution"
  assume_role_policy = data.aws_iam_policy_document.step_functions_assume_role.json
}

data "aws_iam_policy_document" "step_functions_execution_permissions" {
  statement {
    sid    = "InvokeLambdas"
    effect = "Allow"
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.create_branches.arn,
      aws_lambda_function.mock_build.arn,
      aws_lambda_function.prepare_execution.arn,
      aws_lambda_function.analyze_logs.arn,
      aws_lambda_function.generate_report.arn,
      aws_lambda_function.raise_pr.arn,
      aws_lambda_function.read_validation_results.arn,
      aws_lambda_function.await_approval.arn,
    ]
  }

  statement {
    sid    = "EmrServerless"
    effect = "Allow"
    actions = [
      "emr-serverless:StartJobRun",
      "emr-serverless:GetJobRun",
      "emr-serverless:CancelJobRun",
      "emr-serverless:TagResource",
    ]
    resources = [
      aws_emrserverless_application.baseline.arn,
      aws_emrserverless_application.target.arn,
      "${aws_emrserverless_application.baseline.arn}/jobruns/*",
      "${aws_emrserverless_application.target.arn}/jobruns/*",
    ]
  }

  statement {
    sid       = "PassEmrExecutionRole"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.emr_serverless_execution.arn]
  }

  # Required for the .sync service integration pattern - Step Functions
  # manages an EventBridge rule internally to know when the EMR job run
  # actually completes, rather than polling itself.
  statement {
    sid    = "EmrServerlessSyncEventBridge"
    effect = "Allow"
    actions = [
      "events:PutRule",
      "events:PutTargets",
      "events:DescribeRule",
      "events:EnableRule",
      "events:DisableRule",
      "events:RemoveTargets",
      "events:DeleteRule",
    ]
    # The exact managed rule name Step Functions creates for a .sync
    # integration isn't something worth hardcoding and risking a typo on
    # (already got the exact name wrong once: singular "Event" vs AWS's
    # actual "Events") - wildcard-matching all AWS-managed sync-integration
    # rules is the standard, safer pattern.
    resources = [
      "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:rule/StepFunctionsGetEventsFor*",
    ]
  }
}

resource "aws_iam_role_policy" "step_functions_execution" {
  name   = "${var.project_name}-step-functions-execution-permissions"
  role   = aws_iam_role.step_functions_execution.id
  policy = data.aws_iam_policy_document.step_functions_execution_permissions.json
}

resource "aws_sfn_state_machine" "orchestrator" {
  name     = "${var.project_name}-orchestrator"
  role_arn = aws_iam_role.step_functions_execution.arn

  definition = templatefile("${path.module}/state_machine.json.tpl", {
    create_branches_arn          = aws_lambda_function.create_branches.arn
    mock_build_arn               = aws_lambda_function.mock_build.arn
    prepare_execution_arn        = aws_lambda_function.prepare_execution.arn
    analyze_logs_arn             = aws_lambda_function.analyze_logs.arn
    generate_report_arn          = aws_lambda_function.generate_report.arn
    raise_pr_arn                 = aws_lambda_function.raise_pr.arn
    read_validation_results_arn  = aws_lambda_function.read_validation_results.arn
    await_approval_arn           = aws_lambda_function.await_approval.arn
  })

  # Phase 14.6: full state-transition detail in CloudWatch Logs, not just
  # the bounded Step Functions execution-history API - see observability.tf.
  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.state_machine.arn}:*"
    include_execution_data = true
    level                   = "ALL"
  }

  depends_on = [
    aws_iam_role_policy.step_functions_logging,
    aws_cloudwatch_log_resource_policy.state_machine_log_delivery,
  ]
}
