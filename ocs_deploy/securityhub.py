from aws_cdk import Stack, CfnOutput, aws_securityhub as securityhub
from constructs import Construct
from ocs_deploy.config import OCSConfig

class SecurityHubStack(Stack):
    def __init__(self, scope: Construct, config: OCSConfig, **kwargs) -> None:
        super().__init__(scope, config.stack_name("securityhub"), env=config.cdk_env(), **kwargs)
        self.config = config
        self.hub = self.enable_security_hub()

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

        standards_subscription = securityhub.CfnStandardsSubscription(
            self,
            "FoundationalSecurityBestPractices",
            standards_arn=f"arn:aws:securityhub:{self.region}::standards/aws-foundational-security-best-practices/v/1.0.0",
        )
        # Ensure the subscription is created after Security Hub is enabled
        standards_subscription.node.add_dependency(self.hub)
        CfnOutput(
            self,
            self.config.make_name("SecurityHubStandardsSubscriptionArn"),
            value=standards_subscription.attr_standards_subscription_arn,
            description="ARN of the AWS Foundational Security Best Practices subscription",
        )
        return standards_subscription