from aws_cdk import Stack, CfnOutput, aws_securityhub as securityhub
from constructs import Construct
from ocs_deploy.config import OCSConfig

class SecurityHubStack(Stack):
    def __init__(self, scope: Construct, config: OCSConfig, **kwargs) -> None:
        super().__init__(scope, config.stack_name("securityhub"), env=config.cdk_env(), **kwargs)
        self.config = config
        self.hub = self.enable_security_hub()
       # self.enable_standards()

    # Enables Security Hub with auto-enabled controls for new services and integrations
    def enable_security_hub(self) -> securityhub.CfnHub:
        hub = securityhub.CfnHub(
            self,
            "SecurityHub",
            auto_enable_controls=True,
            enable_default_standards=True,
        )
        CfnOutput(
            self,
            self.config.make_name("SecurityHubArn"),
            value=hub.attr_arn,
            description="ARN of the Security Hub",
        )
        return hub

    # Subscribes to CIS AWS Foundations Benchmark v1.2.0 and AWS Foundational Security Best Practices v1.0.0
    def enable_standards(self) -> None:
        '''
        cis_v1_2_0 = securityhub.CfnProductSubscription(
            self,
            "CISAWSFoundationsBenchmarkV1_2_0",
            product_arn="arn:aws:securityhub:us-east-1::standards/cis-aws-foundations-benchmark/v/1.2.0",
        )
        aws_foundational = securityhub.CfnProductSubscription(
            self,
            "AWSFoundationalSecurityBestPractices",
            product_arn="arn:aws:securityhub:us-east-1::standards/aws-foundational-security-best-practices/v/1.0.0",
        )
        cis_v1_2_0.node.add_dependency(self.hub)
        aws_foundational.node.add_dependency(self.hub)
        '''
