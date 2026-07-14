"""Factory for AWS clients. use_mocks=True (local dev) backs everything with
moto; use_mocks=False (real AWS, Step 14) returns plain boto3 clients against
real infrastructure with no other code changes required.
"""

from __future__ import annotations

import boto3


class AWSClientFactory:
    def __init__(self, use_mocks: bool = True, region_name: str = "us-east-1"):
        self.use_mocks = use_mocks
        self.region_name = region_name
        self._mock = None

        if self.use_mocks:
            # Imported lazily, not at module level: moto is a dev/test-only
            # dependency, deliberately excluded from the Lambda layer's
            # runtime deps (infra/lambda-requirements.txt) since it's
            # unused and unwanted in production. A module-level import
            # broke every Lambda handler that transitively imports this
            # module via StateStore, even though use_mocks=False never
            # actually touches moto - confirmed by a real failed Lambda
            # invocation (ImportModuleError: No module named 'moto').
            from moto import mock_aws

            self._mock = mock_aws()
            self._mock.start()

    def get_s3_client(self):
        return boto3.client("s3", region_name=self.region_name)

    def get_dynamodb_resource(self):
        return boto3.resource("dynamodb", region_name=self.region_name)

    def get_sns_client(self):
        return boto3.client("sns", region_name=self.region_name)

    def stop(self) -> None:
        if self._mock is not None:
            self._mock.stop()
            self._mock = None

    def __enter__(self) -> "AWSClientFactory":
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
