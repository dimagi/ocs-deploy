import aws_cdk as cdk
from aws_cdk import aws_s3 as s3
from constructs import Construct

from ocs_deploy.config import OCSConfig


class S3Stack(cdk.Stack):
    def __init__(self, scope: Construct, config: OCSConfig) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.S3_STACK), env=config.cdk_env()
        )

        # Create a public bucket for storing public media files
        s3.Bucket(
            self,
            config.s3_public_bucket_name,
            bucket_name=config.s3_public_bucket_name,
            public_read_access=True,
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=False,
                block_public_policy=False,
                ignore_public_acls=False,
                restrict_public_buckets=False,
            ),
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=False,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # Create a bucket for storing audio files that expire after 30 days
        s3.Bucket(
            self,
            config.s3_whatsapp_audio_bucket,
            bucket_name=config.s3_whatsapp_audio_bucket,
            public_read_access=True,
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=False,
                block_public_policy=False,
                ignore_public_acls=False,
                restrict_public_buckets=False,
            ),
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=False,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            lifecycle_rules=[
                s3.LifecycleRule(
                    expiration=cdk.Duration.days(30),
                )
            ],
        )

        # Create a private bucket for storing private files
        s3.Bucket(
            self,
            config.s3_private_bucket_name,
            bucket_name=config.s3_private_bucket_name,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=False,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
