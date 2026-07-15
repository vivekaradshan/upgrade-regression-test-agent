{
  "Comment": "Upgrade regression test orchestrator - mirrors src/orchestrator/graph.py's LangGraph structure",
  "StartAt": "CreateBranches",
  "States": {
    "CreateBranches": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": { "FunctionName": "${create_branches_arn}", "Payload.$": "$" },
      "ResultSelector": { "payload.$": "$.Payload" },
      "ResultPath": "$.stepResult",
      "OutputPath": "$.stepResult.payload",
      "Next": "MockBuild"
    },
    "MockBuild": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": { "FunctionName": "${mock_build_arn}", "Payload.$": "$" },
      "ResultSelector": { "payload.$": "$.Payload" },
      "ResultPath": "$.stepResult",
      "OutputPath": "$.stepResult.payload",
      "Next": "CheckRetry"
    },
    "CheckRetry": {
      "Type": "Choice",
      "Choices": [
        { "Variable": "$.retry_count", "NumericGreaterThan": 0, "Next": "SetTargetOnlyVariant" }
      ],
      "Default": "ExecuteBoth"
    },
    "ExecuteBoth": {
      "Type": "Parallel",
      "Branches": [
        {
          "StartAt": "SetBaselineVariant",
          "States": {
            "SetBaselineVariant": {
              "Type": "Pass",
              "Result": "baseline",
              "ResultPath": "$.variant",
              "Next": "PrepareBaseline"
            },
            "PrepareBaseline": {
              "Type": "Task",
              "Resource": "arn:aws:states:::lambda:invoke",
              "Parameters": { "FunctionName": "${prepare_execution_arn}", "Payload.$": "$" },
              "ResultSelector": { "payload.$": "$.Payload" },
              "ResultPath": "$.stepResult",
              "OutputPath": "$.stepResult.payload",
              "Next": "RunBaselineEMR"
            },
            "RunBaselineEMR": {
              "Type": "Task",
              "Resource": "arn:aws:states:::emr-serverless:startJobRun.sync",
              "Parameters": {
                "ApplicationId.$": "$.emr_job.applicationId",
                "ExecutionRoleArn.$": "$.emr_job.executionRoleArn",
                "JobDriver": {
                  "SparkSubmit": {
                    "EntryPoint.$": "$.emr_job.entryPoint",
                    "EntryPointArguments.$": "$.emr_job.entryPointArguments",
                    "SparkSubmitParameters.$": "$.emr_job.sparkSubmitParameters"
                  }
                },
                "ConfigurationOverrides": {
                  "MonitoringConfiguration": {
                    "S3MonitoringConfiguration": { "LogUri.$": "$.emr_job.logUri" }
                  }
                }
              },
              "ResultSelector": {
                "status.$": "$.State",
                "jobRunId.$": "$.JobRunId"
              },
              "ResultPath": "$.baseline_execution",
              "Catch": [
                {
                  "ErrorEquals": ["States.ALL"],
                  "ResultPath": "$.errorInfo",
                  "Next": "BaselineFailed"
                }
              ],
              "End": true
            },
            "BaselineFailed": {
              "Type": "Pass",
              "Parameters": {
                "status": "FAILED",
                "cause.$": "$.errorInfo.Cause"
              },
              "ResultPath": "$.baseline_execution",
              "End": true
            }
          }
        },
        {
          "StartAt": "SetTargetVariant",
          "States": {
            "SetTargetVariant": {
              "Type": "Pass",
              "Result": "target",
              "ResultPath": "$.variant",
              "Next": "PrepareTarget"
            },
            "PrepareTarget": {
              "Type": "Task",
              "Resource": "arn:aws:states:::lambda:invoke",
              "Parameters": { "FunctionName": "${prepare_execution_arn}", "Payload.$": "$" },
              "ResultSelector": { "payload.$": "$.Payload" },
              "ResultPath": "$.stepResult",
              "OutputPath": "$.stepResult.payload",
              "Next": "RunTargetEMR"
            },
            "RunTargetEMR": {
              "Type": "Task",
              "Resource": "arn:aws:states:::emr-serverless:startJobRun.sync",
              "Parameters": {
                "ApplicationId.$": "$.emr_job.applicationId",
                "ExecutionRoleArn.$": "$.emr_job.executionRoleArn",
                "JobDriver": {
                  "SparkSubmit": {
                    "EntryPoint.$": "$.emr_job.entryPoint",
                    "EntryPointArguments.$": "$.emr_job.entryPointArguments",
                    "SparkSubmitParameters.$": "$.emr_job.sparkSubmitParameters"
                  }
                },
                "ConfigurationOverrides": {
                  "MonitoringConfiguration": {
                    "S3MonitoringConfiguration": { "LogUri.$": "$.emr_job.logUri" }
                  }
                }
              },
              "ResultSelector": {
                "status.$": "$.State",
                "jobRunId.$": "$.JobRunId"
              },
              "ResultPath": "$.target_execution",
              "Catch": [
                {
                  "ErrorEquals": ["States.ALL"],
                  "ResultPath": "$.errorInfo",
                  "Next": "TargetFailed"
                }
              ],
              "End": true
            },
            "TargetFailed": {
              "Type": "Pass",
              "Parameters": {
                "status": "FAILED",
                "cause.$": "$.errorInfo.Cause"
              },
              "ResultPath": "$.target_execution",
              "End": true
            }
          }
        }
      ],
      "ResultSelector": {
        "baseline_execution.$": "$[0].baseline_execution",
        "target_execution.$": "$[1].target_execution",
        "emr_job.$": "$[1].emr_job"
      },
      "ResultPath": "$.executionResults",
      "Next": "CopyBaselineExecution"
    },
    "CopyBaselineExecution": {
      "Type": "Pass",
      "InputPath": "$.executionResults.baseline_execution",
      "ResultPath": "$.baseline_execution",
      "Next": "CopyEmrJob"
    },
    "CopyEmrJob": {
      "Type": "Pass",
      "InputPath": "$.executionResults.emr_job",
      "ResultPath": "$.emr_job",
      "Next": "CopyTargetExecution"
    },
    "CopyTargetExecution": {
      "Type": "Pass",
      "InputPath": "$.executionResults.target_execution",
      "ResultPath": "$.target_execution",
      "Next": "AnalyzeLogs"
    },
    "SetTargetOnlyVariant": {
      "Type": "Pass",
      "Result": "target",
      "ResultPath": "$.variant",
      "Next": "PrepareTargetOnly"
    },
    "PrepareTargetOnly": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": { "FunctionName": "${prepare_execution_arn}", "Payload.$": "$" },
      "ResultSelector": { "payload.$": "$.Payload" },
      "ResultPath": "$.stepResult",
      "OutputPath": "$.stepResult.payload",
      "Next": "RunTargetEMROnly"
    },
    "RunTargetEMROnly": {
      "Type": "Task",
      "Resource": "arn:aws:states:::emr-serverless:startJobRun.sync",
      "Parameters": {
        "ApplicationId.$": "$.emr_job.applicationId",
        "ExecutionRoleArn.$": "$.emr_job.executionRoleArn",
        "JobDriver": {
          "SparkSubmit": {
            "EntryPoint.$": "$.emr_job.entryPoint",
            "EntryPointArguments.$": "$.emr_job.entryPointArguments",
            "SparkSubmitParameters.$": "$.emr_job.sparkSubmitParameters"
          }
        },
        "ConfigurationOverrides": {
          "MonitoringConfiguration": {
            "S3MonitoringConfiguration": { "LogUri.$": "$.emr_job.logUri" }
          }
        }
      },
      "ResultSelector": {
        "status.$": "$.State",
        "jobRunId.$": "$.JobRunId"
      },
      "ResultPath": "$.target_execution",
      "Catch": [
        {
          "ErrorEquals": ["States.ALL"],
          "ResultPath": "$.errorInfo",
          "Next": "TargetOnlyFailed"
        }
      ],
      "Next": "AnalyzeLogs"
    },
    "TargetOnlyFailed": {
      "Type": "Pass",
      "Parameters": {
        "status": "FAILED",
        "cause.$": "$.errorInfo.Cause"
      },
      "ResultPath": "$.target_execution",
      "Next": "AnalyzeLogs"
    },
    "AnalyzeLogs": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": { "FunctionName": "${analyze_logs_arn}", "Payload.$": "$" },
      "ResultSelector": { "payload.$": "$.Payload" },
      "ResultPath": "$.stepResult",
      "OutputPath": "$.stepResult.payload",
      "Next": "RouteAfterAnalysis"
    },
    "RouteAfterAnalysis": {
      "Type": "Choice",
      "Choices": [
        { "Variable": "$.phase", "StringEquals": "RETRY", "Next": "CheckRetry" },
        { "Variable": "$.phase", "StringEquals": "VALIDATE", "Next": "SetValidateVariant" }
      ],
      "Default": "GenerateReport"
    },
    "SetValidateVariant": {
      "Type": "Pass",
      "Result": "validate",
      "ResultPath": "$.variant",
      "Next": "PrepareValidate"
    },
    "PrepareValidate": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": { "FunctionName": "${prepare_execution_arn}", "Payload.$": "$" },
      "ResultSelector": { "payload.$": "$.Payload" },
      "ResultPath": "$.stepResult",
      "OutputPath": "$.stepResult.payload",
      "Next": "RunValidateEMR"
    },
    "RunValidateEMR": {
      "Type": "Task",
      "Resource": "arn:aws:states:::emr-serverless:startJobRun.sync",
      "Parameters": {
        "ApplicationId.$": "$.emr_job.applicationId",
        "ExecutionRoleArn.$": "$.emr_job.executionRoleArn",
        "JobDriver": {
          "SparkSubmit": {
            "EntryPoint.$": "$.emr_job.entryPoint",
            "EntryPointArguments.$": "$.emr_job.entryPointArguments",
            "SparkSubmitParameters.$": "$.emr_job.sparkSubmitParameters"
          }
        },
        "ConfigurationOverrides": {
          "MonitoringConfiguration": {
            "S3MonitoringConfiguration": { "LogUri.$": "$.emr_job.logUri" }
          }
        }
      },
      "ResultPath": "$.validateEmrResult",
      "Next": "ReadValidationResults"
    },
    "ReadValidationResults": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": { "FunctionName": "${read_validation_results_arn}", "Payload.$": "$" },
      "ResultSelector": { "payload.$": "$.Payload" },
      "ResultPath": "$.stepResult",
      "OutputPath": "$.stepResult.payload",
      "Next": "GenerateReport"
    },
    "GenerateReport": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": { "FunctionName": "${generate_report_arn}", "Payload.$": "$" },
      "ResultSelector": { "payload.$": "$.Payload" },
      "ResultPath": "$.stepResult",
      "OutputPath": "$.stepResult.payload",
      "Next": "RaisePR"
    },
    "RaisePR": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": { "FunctionName": "${raise_pr_arn}", "Payload.$": "$" },
      "ResultSelector": { "payload.$": "$.Payload" },
      "ResultPath": "$.stepResult",
      "OutputPath": "$.stepResult.payload",
      "End": true
    }
  }
}
