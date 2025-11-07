# ocs_deploy/stacks/waf.py
from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_wafv2 as wafv2,
    aws_logs as logs,
    aws_iam as iam,
    CfnOutput,
)
from constructs import Construct
from ocs_deploy.config import OCSConfig
from typing import List

from ocs_deploy.waf_utils import (
    NoUserAgent_HEADER,
    SizeRestrictions_BODY,
    create_waf_regex_groupings,
)


class WAFStack(Stack):
    """
    Represents a CDK stack for deploying a WAF Web ACL associated with an Application Load Balancer.

    Protection Strategy:
    1. IP blocklists (highest priority) - block malicious IPs
    2. Scope-down allow rules - allow specific conditions to bypass ONLY specific checks:
       - Large body (>8KB) for specific paths: bypass SizeRestrictions_BODY only when body is actually large
       - Missing User-Agent for specific paths: bypass NoUserAgent_HEADER only when header is actually missing
    3. AWS Managed Rules Common Rule Set - applies to all other traffic
    4. Rate limiting (COUNT mode) - 2000 req/IP/5min

    Logs all requests to CloudWatch for monitoring and analysis.
    """

    def _create_regex_match_statement(
        self, pattern_set_arn: str
    ) -> wafv2.CfnWebACL.StatementProperty:
        """Helper to create a regex pattern set match statement for URI path."""
        return wafv2.CfnWebACL.StatementProperty(
            regex_pattern_set_reference_statement=wafv2.CfnWebACL.RegexPatternSetReferenceStatementProperty(
                arn=pattern_set_arn,
                field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(uri_path={}),
                text_transformations=[
                    wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="NONE")
                ],
            )
        )

    def _create_uri_matches_any_pattern_set_statement(
        self, pattern_sets: List[wafv2.CfnRegexPatternSet]
    ) -> wafv2.CfnWebACL.StatementProperty:
        """Create an OR statement that matches if URI matches any of the pattern sets."""
        if len(pattern_sets) == 1:
            return self._create_regex_match_statement(pattern_sets[0].attr_arn)
        else:
            return wafv2.CfnWebACL.StatementProperty(
                or_statement=wafv2.CfnWebACL.OrStatementProperty(
                    statements=[
                        self._create_regex_match_statement(ps.attr_arn)
                        for ps in pattern_sets
                    ]
                )
            )

    def _create_missing_user_agent_statement(self) -> wafv2.CfnWebACL.StatementProperty:
        """Create statement that checks if User-Agent header is missing or empty."""
        return wafv2.CfnWebACL.StatementProperty(
            not_statement=wafv2.CfnWebACL.NotStatementProperty(
                statement=wafv2.CfnWebACL.StatementProperty(
                    size_constraint_statement=wafv2.CfnWebACL.SizeConstraintStatementProperty(
                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(
                                name="user-agent"
                            )
                        ),
                        comparison_operator="GT",
                        size=0,
                        text_transformations=[
                            wafv2.CfnWebACL.TextTransformationProperty(
                                priority=0, type="NONE"
                            )
                        ],
                    )
                )
            )
        )

    def _create_large_body_statement(self) -> wafv2.CfnWebACL.StatementProperty:
        """Create statement that checks if body size is > 8KB."""
        return wafv2.CfnWebACL.StatementProperty(
            size_constraint_statement=wafv2.CfnWebACL.SizeConstraintStatementProperty(
                field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                    body=wafv2.CfnWebACL.BodyProperty(oversize_handling="CONTINUE")
                ),
                comparison_operator="GT",
                size=8192,  # 8KB threshold
                text_transformations=[
                    wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="NONE")
                ],
            )
        )

    def __init__(
        self, scope: Construct, config: OCSConfig, load_balancer_arn: str, **kwargs
    ) -> None:
        super().__init__(
            scope, config.stack_name("waf"), env=config.cdk_env(), **kwargs
        )
        self.config = config

        # Create IP sets for blocking malicious traffic
        temp_block_ipset = wafv2.CfnIPSet(
            self,
            "TempBlockIPSet",
            scope="REGIONAL",
            name=config.make_name("TempBlockIPs"),
            description="Temporary IP blocklist - managed via IaC",
            addresses=[],  # Empty by default, add IPs as needed
            ip_address_version="IPV4",
        )

        permanent_block_ipset = wafv2.CfnIPSet(
            self,
            "PermanentBlockIPSet",
            scope="REGIONAL",
            name=config.make_name("PermanentBlockIPs"),
            description="Permanent IP blocklist - managed via AWS Console",
            addresses=[],  # Empty by default, managed outside IaC
            ip_address_version="IPV4",
        )

        # Create regex pattern sets for scope-down allow rules
        # Use compacting to reduce pattern count and split into groups if needed
        large_body_groups = create_waf_regex_groupings(SizeRestrictions_BODY)
        no_user_agent_groups = create_waf_regex_groupings(NoUserAgent_HEADER)

        # Create pattern sets for large body paths (may be multiple sets)
        large_body_pattern_sets = []
        for idx, pattern_group in enumerate(large_body_groups):
            pattern_set = wafv2.CfnRegexPatternSet(
                self,
                f"LargeBodyPathsPatternSet{idx}",
                scope="REGIONAL",
                name=config.make_name(f"LargeBodyPaths{idx}"),
                description=f"Paths that can send large POST bodies - bypass SizeRestrictions_BODY only: group {idx}",
                regular_expression_list=list(pattern_group),
            )
            large_body_pattern_sets.append(pattern_set)

        # Create pattern sets for no user agent paths (may be multiple sets)
        no_user_agent_pattern_sets = []
        for idx, pattern_group in enumerate(no_user_agent_groups):
            pattern_set = wafv2.CfnRegexPatternSet(
                self,
                f"NoUserAgentPathsPatternSet{idx}",
                scope="REGIONAL",
                name=config.make_name(f"NoUserAgentPaths{idx}"),
                description=f"Paths that can omit User-Agent header - bypass NoUserAgent_HEADER only: group {idx}",
                regular_expression_list=list(pattern_group),
            )
            no_user_agent_pattern_sets.append(pattern_set)

        # Define the Web ACL with rules
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
                # Rule 0: Block temporary IPs (highest priority)
                wafv2.CfnWebACL.RuleProperty(
                    name="BlockTempIPs",
                    priority=0,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        ip_set_reference_statement=wafv2.CfnWebACL.IPSetReferenceStatementProperty(
                            arn=temp_block_ipset.attr_arn,
                        )
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name=config.make_name("BlockTempIPsMetrics"),
                        sampled_requests_enabled=True,
                    ),
                ),
                # Rule 1: Block permanent IPs
                wafv2.CfnWebACL.RuleProperty(
                    name="BlockPermanentIPs",
                    priority=1,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        ip_set_reference_statement=wafv2.CfnWebACL.IPSetReferenceStatementProperty(
                            arn=permanent_block_ipset.attr_arn,
                        )
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name=config.make_name("BlockPermanentIPsMetrics"),
                        sampled_requests_enabled=True,
                    ),
                ),
                # Rule 2: Allow paths with missing User-Agent header (scope-down rule)
                # Matches requests to specific paths that don't have User-Agent header
                wafv2.CfnWebACL.RuleProperty(
                    name="AllowNoUserAgentForSpecificPaths",
                    priority=2,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        and_statement=wafv2.CfnWebACL.AndStatementProperty(
                            statements=[
                                # Statement 1: URI matches any of the no user agent pattern sets
                                self._create_uri_matches_any_pattern_set_statement(
                                    no_user_agent_pattern_sets
                                ),
                                # Statement 2: User-Agent header is missing or empty
                                self._create_missing_user_agent_statement(),
                            ]
                        )
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(allow={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name=config.make_name("AllowNoUserAgentMetrics"),
                        sampled_requests_enabled=True,
                    ),
                ),
                # Rule 3: Allow large body for specific paths (scope-down rule)
                # Matches requests to specific paths that have body size > 8KB
                wafv2.CfnWebACL.RuleProperty(
                    name="AllowLargeBodyForSpecificPaths",
                    priority=3,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        and_statement=wafv2.CfnWebACL.AndStatementProperty(
                            statements=[
                                # Statement 1: URI matches any of the large body pattern sets
                                self._create_uri_matches_any_pattern_set_statement(
                                    large_body_pattern_sets
                                ),
                                # Statement 2: Body size is greater than 8KB
                                self._create_large_body_statement(),
                            ]
                        )
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(allow={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name=config.make_name("AllowLargeBodyPathsMetrics"),
                        sampled_requests_enabled=True,
                    ),
                ),
                # Rule 4: AWS Managed Common Rule Set (Count mode)
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedCommonRuleSet",
                    priority=4,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesCommonRuleSet",
                        )
                    ),
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(count={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name=config.make_name("CommonRuleSetMetrics"),
                        sampled_requests_enabled=True,
                    ),
                ),
                # Rule 5: Rate Limiting (Count mode)
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimitRule",
                    priority=5,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=2000,
                            aggregate_key_type="IP",
                        )
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(count={}),
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

        # Create a CloudWatch Log Group for WAF logs with the required prefix
        log_group = logs.LogGroup(
            self,
            "WAFLogGroup",
            log_group_name=f"aws-waf-logs-{config.make_name('waf-logs')}",
            retention=logs.RetentionDays.TWO_YEARS,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # Add a resource policy to allow WAF to write logs
        log_group.add_to_resource_policy(
            statement=iam.PolicyStatement(
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                principals=[iam.ServicePrincipal("wafv2.amazonaws.com")],
                resources=[log_group.log_group_arn],
            )
        )

        # Add WAF Logging Configuration
        logging_config = wafv2.CfnLoggingConfiguration(
            self,
            "WAFLoggingConfig",
            resource_arn=self.web_acl.attr_arn,
            log_destination_configs=[log_group.log_group_arn.replace(":*", "")],
        )

        # Ensure the log group policy is applied before the logging configuration
        logging_config.node.add_dependency(log_group)

        # Output the Web ACL ARN
        CfnOutput(
            self,
            config.make_name("WebACLArn"),
            value=self.web_acl.attr_arn,
            description="ARN of the WAF Web ACL",
        )

        # Output the Log Group ARN
        CfnOutput(
            self,
            config.make_name("WAFLogGroupArn"),
            value=log_group.log_group_arn,
            description="ARN of the WAF Log Group",
        )

        # Output IP Set ARNs for easy reference
        CfnOutput(
            self,
            config.make_name("TempBlockIPSetArn"),
            value=temp_block_ipset.attr_arn,
            description="ARN of the temporary IP blocklist",
        )

        CfnOutput(
            self,
            config.make_name("PermanentBlockIPSetArn"),
            value=permanent_block_ipset.attr_arn,
            description="ARN of the permanent IP blocklist",
        )
