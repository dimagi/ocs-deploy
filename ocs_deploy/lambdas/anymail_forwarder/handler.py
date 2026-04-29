"""Forward SES inbound SNS notifications to anymail's webhook with HTTP Basic auth.

CFN does not resolve `{{resolve:secretsmanager:...}}` dynamic references in
`AWS::SNS::Subscription.Endpoint`, which prevents embedding the webhook secret
directly in an HTTPS subscription URL. SNS instead invokes this Lambda, which
fetches the secret at runtime and re-issues the SNS notification to anymail.
"""

import base64
import json
import os
import urllib.request

import boto3

SECRET_NAME = os.environ["ANYMAIL_WEBHOOK_SECRET_NAME"]
WEBHOOK_URL = os.environ["ANYMAIL_WEBHOOK_URL"]
HTTP_TIMEOUT_SECONDS = 10

_secrets_client = boto3.client("secretsmanager")
_cached_secret: str | None = None


def _get_secret() -> str:
    global _cached_secret
    if _cached_secret is None:
        response = _secrets_client.get_secret_value(SecretId=SECRET_NAME)
        _cached_secret = response["SecretString"]
    return _cached_secret


def _basic_auth_header() -> str:
    secret = _get_secret()
    creds = f"{secret}:{secret}".encode()
    return "Basic " + base64.b64encode(creds).decode("ascii")


def _sns_http_payload(sns: dict) -> bytes:
    payload = {
        "Type": sns.get("Type", "Notification"),
        "MessageId": sns["MessageId"],
        "TopicArn": sns["TopicArn"],
        "Subject": sns.get("Subject"),
        "Message": sns["Message"],
        "Timestamp": sns["Timestamp"],
        "SignatureVersion": sns.get("SignatureVersion", "1"),
        "Signature": sns.get("Signature", ""),
        "SigningCertURL": sns.get("SigningCertUrl", ""),
        "UnsubscribeURL": sns.get("UnsubscribeUrl", ""),
    }
    return json.dumps(payload).encode("utf-8")


def handler(event, _context):
    auth_header = _basic_auth_header()
    for record in event.get("Records", []):
        sns = record["Sns"]
        request = urllib.request.Request(
            WEBHOOK_URL,
            data=_sns_http_payload(sns),
            method="POST",
            headers={
                "Content-Type": "text/plain; charset=UTF-8",
                "Authorization": auth_header,
                "x-amz-sns-message-type": sns.get("Type", "Notification"),
                "x-amz-sns-message-id": sns["MessageId"],
                "x-amz-sns-topic-arn": sns["TopicArn"],
            },
        )
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS):
            pass
