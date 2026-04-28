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
                                            "aws:SourceAccount": "111111111111"
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
    template.has_resource_properties(
        "AWS::SecretsManager::Secret",
        {
            "Name": "ocs/test/anymail-webhook-secret",
            "GenerateSecretString": assertions.Match.object_like(
                {
                    "ExcludeCharacters": assertions.Match.string_like_regexp(
                        ".*[:/@].*"
                    ),
                    "PasswordLength": 32,
                }
            ),
        },
    )


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


def test_receipt_rule_actions_are_s3_then_sns(ocs_config):
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
                                    {"ObjectKeyPrefix": "inbound/"}
                                )
                            }
                        ),
                        assertions.Match.object_like(
                            {
                                "SNSAction": assertions.Match.object_like(
                                    {"Encoding": "Base64"}
                                )
                            }
                        ),
                    ],
                }
            ),
        },
    )
