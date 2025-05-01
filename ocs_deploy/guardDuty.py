from aws_cdk import Stack, RemovalPolicy, Duration, CfnOutput, aws_guardduty as guardduty, aws_s3 as s3, aws_iam as iam
from constructs import Construct
from ocs_deploy.config import OCSConfig

class GuardDutyStack(Stack):
    def __init__(self, scope: Construct, config: OCSConfig, **kwargs) -> None:
        super().__init__(scope, config.stack_name("guardduty"), env=config.cdk_env(), **kwargs)
        self.config = config
        self.findings_bucket = self.create_findings_bucket()
        self.detector = self.enable_guardduty()

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
                guardduty.CfnDetector.CFNFeatureConfigurationProperty(
                    name="RUNTIME_MONITORING",
                    status="ENABLED",
                    additional_configuration=[
                        guardduty.CfnDetector.CFNFeatureAdditionalConfigurationProperty(
                            name="EKS_ADDON_MANAGEMENT", status="ENABLED"
                        ),
                        guardduty.CfnDetector.CFNFeatureAdditionalConfigurationProperty(
                            name="ECS_FARGATE_AGENT_MANAGEMENT", status="ENABLED"
                        ),
                    ],
                ),
                guardduty.CfnDetector.CFNFeatureConfigurationProperty(
                    name="LAMBDA_NETWORK_LOGS",
                    status="ENABLED",
                ),
                guardduty.CfnDetector.CFNFeatureConfigurationProperty(
                    name="RDS_LOGIN_EVENTS",
                    status="ENABLED",
                ),
                guardduty.CfnDetector.CFNFeatureConfigurationProperty(
                    name="EKS_AUDIT_LOGS",
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