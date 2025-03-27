# ocs_deploy/stacks/waf.py
from aws_cdk import Stack, aws_wafv2 as wafv2, CfnOutput
from constructs import Construct
from ocs_deploy.config import OCSConfig

class WAFStack(Stack):
    """
    Represents a CDK stack for deploying a WAF Web ACL associated with an Application Load Balancer.
    Includes AWS Managed Rules and rate limiting for the Django app.
    """

    def __init__(
        self,
        scope: Construct,
        config: OCSConfig,
        load_balancer_arn: str,
        **kwargs
    ) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.WAF_STACK), env=config.cdk_env(), **kwargs
        )
        self.config = config

        # Define the Web ACL
        self.web_acl = wafv2.CfnWebACL(
            self,
            "DjangoWebACL",
            name=config.make_name("DjangoWAF"),
            scope="REGIONAL",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=config.make_name("DjangoWAFMetrics"),
                sampled_requests_enabled=True,
            ),
            rules=[
                # Rule 1: AWS Managed Common Rule Set
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedCommonRuleSet",
                    priority=0,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesCommonRuleSet",
                        )
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(count={}),  # Changed to count
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name=config.make_name("CommonRuleSetMetrics"),
                        sampled_requests_enabled=True,
                    ),
                ),
                # Rule 2: Rate Limiting (2000 requests per 5 minutes per IP)

                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimitRule",
                    priority=1,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=2000,  # 2000 requests per 5-minute window
                            aggregate_key_type="IP",
                        )
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(count={}),  # Changed to count
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name=config.make_name("RateLimitMetrics"),
                        sampled_requests_enabled=True,
                    ),
                ),
            ],
        )

        # Associate with the ALB
        wafv2.CfnWebACLAssociation(
            self,
            "WAFAssociation",
            web_acl_arn=self.web_acl.attr_arn,
            resource_arn=load_balancer_arn,
        )

        # Output the Web ACL ARN
        CfnOutput(
            self,
            config.make_name("WebACLArn"),
            value=self.web_acl.attr_arn,
            description="ARN of the WAF Web ACL",
        )