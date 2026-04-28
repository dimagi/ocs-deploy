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
