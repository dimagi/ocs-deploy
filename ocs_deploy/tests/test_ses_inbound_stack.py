import aws_cdk as cdk
import aws_cdk.assertions as assertions

from ocs_deploy.ses_inbound import SesInboundStack


def _synth(config):
    app = cdk.App()
    stack = SesInboundStack(app, config)
    return assertions.Template.from_stack(stack)


def test_bucket_is_private(ocs_config):
    template = _synth(ocs_config)
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
        },
    )


def test_bucket_has_seven_day_lifecycle(ocs_config):
    template = _synth(ocs_config)
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "LifecycleConfiguration": {
                "Rules": [
                    {
                        "Status": "Enabled",
                        "ExpirationInDays": 7,
                        "Prefix": "inbound/",
                    }
                ],
            },
        },
    )


def test_bucket_policy_allows_ses_putobject(ocs_config):
    template = _synth(ocs_config)
    template.has_resource_properties(
        "AWS::S3::BucketPolicy",
        assertions.Match.object_like(
            {
                "PolicyDocument": {
                    "Statement": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {
                                    "Effect": "Allow",
                                    "Principal": {"Service": "ses.amazonaws.com"},
                                    "Action": "s3:PutObject",
                                    "Condition": {
                                        "StringEquals": {
                                            "aws:SourceAccount": "111111111111",
                                            "aws:SourceArn": assertions.Match.string_like_regexp(
                                                r"arn:aws:ses:us-east-1:111111111111:receipt-rule-set/.*:receipt-rule/\*$"
                                            ),
                                        }
                                    },
                                }
                            )
                        ]
                    ),
                }
            }
        ),
    )


def test_sns_topic_created(ocs_config):
    template = _synth(ocs_config)
    template.resource_count_is("AWS::SNS::Topic", 1)


def test_anymail_webhook_secret_excludes_url_unsafe_chars(ocs_config):
    template = _synth(ocs_config)
    secrets = template.find_resources("AWS::SecretsManager::Secret")
    assert len(secrets) == 1
    props = next(iter(secrets.values()))["Properties"]
    assert props["Name"] == "ocs/test/anymail-webhook-secret"
    gen = props["GenerateSecretString"]
    assert gen["PasswordLength"] == 32
    excluded = gen["ExcludeCharacters"]
    # Every char that breaks URL embedding must be in the exclude set.
    for required in [":", "/", "@", "?", "#", "%", "[", "]"]:
        assert required in excluded, f"{required!r} must be in ExcludeCharacters"


def test_receipt_rule_set_created(ocs_config):
    template = _synth(ocs_config)
    template.resource_count_is("AWS::SES::ReceiptRuleSet", 1)


def test_receipt_rule_recipients_include_all_domains(ocs_config_factory):
    config = ocs_config_factory(
        EMAIL_DOMAIN="primary.com",
        EMAIL_INBOUND_DOMAINS="a.com,b.com",
    )
    template = _synth(config)
    template.has_resource_properties(
        "AWS::SES::ReceiptRule",
        {
            "Rule": assertions.Match.object_like(
                {
                    "Recipients": ["primary.com", "a.com", "b.com"],
                    "Enabled": True,
                    "ScanEnabled": True,
                }
            ),
        },
    )


def test_receipt_rule_has_single_s3_action_with_sns_topic(ocs_config):
    """Rule has exactly one action — an S3 action that also notifies SNS.

    A separate SNSAction would force SES to inline the email body in the SNS
    notification, which caps inbound mail at 150 KB. Using S3Action.topic
    publishes only a metadata "Received" notification; anymail fetches the
    body from S3 via the Fargate task role.
    """
    template = _synth(ocs_config)
    template.has_resource_properties(
        "AWS::SES::ReceiptRule",
        {
            "Rule": assertions.Match.object_like(
                {
                    "Actions": [
                        assertions.Match.object_like(
                            {
                                "S3Action": assertions.Match.object_like(
                                    {
                                        "ObjectKeyPrefix": "inbound/",
                                        "TopicArn": assertions.Match.any_value(),
                                    }
                                )
                            }
                        ),
                    ],
                }
            ),
        },
    )


def test_receipt_rule_has_no_separate_sns_action(ocs_config):
    """Guard against re-introducing the inline-body SNSAction."""
    template = _synth(ocs_config)
    rules = template.find_resources("AWS::SES::ReceiptRule")
    assert len(rules) == 1
    actions = next(iter(rules.values()))["Properties"]["Rule"]["Actions"]
    for action in actions:
        assert "SNSAction" not in action, (
            f"SNSAction must not appear on the receipt rule (found in {action!r}); "
            "the SNS notification path is now driven by S3Action.topic."
        )


def test_sns_subscription_targets_forwarder_lambda(ocs_config):
    template = _synth(ocs_config)
    subs = template.find_resources("AWS::SNS::Subscription")
    assert len(subs) == 1
    props = next(iter(subs.values()))["Properties"]
    assert props["Protocol"] == "lambda"
    # Endpoint should be a Fn::GetAtt to the forwarder Lambda's ARN, never a
    # raw URL with an embedded `{{resolve:secretsmanager:...}}` reference
    # (CFN does not resolve those for SNS subscription endpoints, which fails
    # at deploy with "Invalid parameter: HTTP(S) Endpoint URL").
    endpoint = props["Endpoint"]
    assert isinstance(endpoint, dict) and "Fn::GetAtt" in endpoint
    assert "{{resolve:secretsmanager:" not in str(endpoint)


def test_forwarder_lambda_has_webhook_url_and_secret_env(ocs_config):
    template = _synth(ocs_config)
    template.has_resource_properties(
        "AWS::Lambda::Function",
        assertions.Match.object_like(
            {
                "Handler": "handler.handler",
                "Environment": {
                    "Variables": {
                        "ANYMAIL_WEBHOOK_SECRET_NAME": "ocs/test/anymail-webhook-secret",
                        "ANYMAIL_WEBHOOK_URL": "https://ocs.example.com/anymail/amazon_ses/inbound/",
                    }
                },
            }
        ),
    )


def test_forwarder_webhook_url_uses_anymail_webhook_domain_override(
    ocs_config_factory,
):
    config = ocs_config_factory(
        DOMAIN_NAME="new.example.com",
        ANYMAIL_WEBHOOK_DOMAIN="legacy.example.com",
    )
    template = _synth(config)
    template.has_resource_properties(
        "AWS::Lambda::Function",
        assertions.Match.object_like(
            {
                "Environment": {
                    "Variables": assertions.Match.object_like(
                        {
                            "ANYMAIL_WEBHOOK_URL": "https://legacy.example.com/anymail/amazon_ses/inbound/",
                        }
                    ),
                },
            }
        ),
    )


def test_forwarder_lambda_can_read_webhook_secret(ocs_config):
    template = _synth(ocs_config)
    template.has_resource_properties(
        "AWS::IAM::Policy",
        assertions.Match.object_like(
            {
                "PolicyDocument": {
                    "Statement": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {
                                    "Effect": "Allow",
                                    "Action": assertions.Match.array_with(
                                        ["secretsmanager:GetSecretValue"]
                                    ),
                                }
                            )
                        ]
                    ),
                }
            }
        ),
    )
