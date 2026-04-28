import aws_cdk as cdk
from aws_cdk import (
    aws_iam as iam,
    aws_s3 as s3,
)
from constructs import Construct

from ocs_deploy.config import OCSConfig

INBOUND_PREFIX = "inbound/"


class SesInboundStack(cdk.Stack):
    """SES inbound mail plumbing: S3 bucket, SNS topic, receipt rules, webhook secret."""

    def __init__(self, scope: Construct, config: OCSConfig) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.SES_INBOUND_STACK), env=config.cdk_env()
        )
        self.config = config
        self.bucket = self._create_bucket()

    def _create_bucket(self) -> s3.Bucket:
        bucket = s3.Bucket(
            self,
            self.config.make_name("SesInboundBucket"),
            bucket_name=self.config.make_name("ses-inbound-mail"),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    enabled=True,
                    prefix=INBOUND_PREFIX,
                    expiration=cdk.Duration.days(7),
                )
            ],
        )
        bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowSESPuts",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("ses.amazonaws.com")],
                actions=["s3:PutObject"],
                resources=[f"{bucket.bucket_arn}/{INBOUND_PREFIX}*"],
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.config.account},
                },
            )
        )
        cdk.CfnOutput(
            self,
            self.config.make_name("SesInboundBucketName"),
            value=bucket.bucket_name,
        )
        return bucket
