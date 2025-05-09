from aws_cdk import Stack, RemovalPolicy, Duration, CfnOutput, aws_guardduty as guardduty, aws_s3 as s3, aws_iam as iam
from constructs import Construct
from ocs_deploy.config import OCSConfig

class GuardDutyStack(Stack):
    """
    This stack sets up AWS GuardDuty for threat detection and monitoring.
    It creates a GuardDuty detector and an S3 bucket for storing findings.
    """
    def __init__(self, scope: Construct, config: OCSConfig, **kwargs) -> None:
        super().__init__(scope, config.stack_name("guardduty"), env=config.cdk_env(), **kwargs)
        self.config = config
        self.findings_bucket = self.create_findings_bucket()
        self.detector = self.enable_guardduty()

    """
    Creates an S3 bucket for GuardDuty findings and configures the bucket policy
    to allow GuardDuty to write findings to the bucket.
    The bucket is versioned, encrypted, and has a lifecycle rule to transition
    objects to Infrequent Access storage class after 90 days and expire them
    after 365 days.
    The bucket policy allows GuardDuty to put objects in the bucket.
    The bucket is retained on stack deletion.
    """
    def create_findings_bucket(self) -> s3.Bucket:
        bucket = s3.Bucket(
            self,
            "GuardDutyFindingsBucket",
            bucket_name=self.config.make_name("guardduty-findings").lower(),
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    enabled=True,
                    expiration=Duration.days(365),
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(90),
                        ),
                    ],
                ),
            ],
        )
        bucket_policy = iam.PolicyStatement(
            actions=["s3:PutObject"],
            principals=[iam.ServicePrincipal("guardduty.amazonaws.com")],
            resources=[f"{bucket.bucket_arn}/*"],
        )
        bucket.add_to_resource_policy(bucket_policy)
        CfnOutput(
            self,
            self.config.make_name("GuardDutyFindingsBucketArn"),
            value=bucket.bucket_arn,
            description="ARN of the S3 bucket for GuardDuty findings",
        )
        return bucket

    """
    Enables GuardDuty with the specified features and configurations.
    The detector is configured to publish findings every 15 minutes.
    The following features are enabled:
    - S3 Data Events
    - EBS Malware Protection
    - Runtime Monitoring
    - RDS Login Events
    The detector ID is outputted for reference.
    """
    def enable_guardduty(self) -> guardduty.CfnDetector:
        detector = guardduty.CfnDetector(
            self,
            "GuardDutyDetector",
            enable=True,
            finding_publishing_frequency="FIFTEEN_MINUTES",
            features=[
                guardduty.CfnDetector.CFNFeatureConfigurationProperty(
                    name="S3_DATA_EVENTS",
                    status="ENABLED",
                ),
                guardduty.CfnDetector.CFNFeatureConfigurationProperty(
                    name="EBS_MALWARE_PROTECTION",
                    status="ENABLED",
                ),
                # comment the following line to disable runtime monitoring
                guardduty.CfnDetector.CFNFeatureConfigurationProperty(
                    name="RDS_LOGIN_EVENTS",
                    status="ENABLED",
                ),
            ],
        )
        CfnOutput(
            self,
            self.config.make_name("GuardDutyDetectorId"),
            value=detector.ref,
            description="ID of the GuardDuty detector",
        )
        return detector