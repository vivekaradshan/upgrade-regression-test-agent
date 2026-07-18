"""SigV4-signed HTTP POST, shared by cli.py and dashboard/app.py.

Both need to call the AWS_IAM-authorized POST /runs endpoint (see
start_run_handler.py / api_gateway.tf) using whatever AWS credentials the
calling process already has - the CLI's local `aws configure` credentials,
or the dashboard's App Runner instance role when running there. Signing
with the caller's own identity avoids issuing a separate API key.
"""

from __future__ import annotations

import json

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session as BotocoreSession


class NoCredentialsError(Exception):
    pass


class SignedRequestError(Exception):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"API request failed ({status_code}): {body}")


def signed_post(url: str, body: dict, region: str) -> dict:
    session = BotocoreSession()
    credentials = session.get_credentials()
    if credentials is None:
        raise NoCredentialsError("No AWS credentials found (run `aws configure` or set AWS_* env vars)")

    request = AWSRequest(method="POST", url=url, data=json.dumps(body), headers={"Content-Type": "application/json"})
    SigV4Auth(credentials, "execute-api", region).add_auth(request)

    response = httpx.post(url, content=request.body, headers=dict(request.headers))
    if response.status_code >= 400:
        raise SignedRequestError(response.status_code, response.text)
    return response.json()
