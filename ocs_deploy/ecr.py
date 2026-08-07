import aws_cdk as cdk
from aws_cdk import (
    aws_ecr as ecr,
)
from constructs import Construct

from ocs_deploy.config import OCSConfig


class EcrStack(cdk.Stack):
    def __init__(self, scope: Construct, config: OCSConfig) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.ECR_STACK), env=config.cdk_env()
        )
        self.repo = self.setup_ecr(config)

    def setup_ecr(self, config: OCSConfig):
        ecr_repo = ecr.Repository(
            self,
            config.make_name("ECR"),
            repository_name=config.ecr_repo_name,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        # ECS resolves the `latest` tag to a digest when a deployment starts and
        # pins it for the life of that deployment, so every later task launch
        # (autoscale-out, task replacement) pulls that exact digest. Pushing a
        # new image untags the old one, so these rules must keep it around long
        # enough for services that haven't redeployed since -- otherwise those
        # launches fail with CannotPullContainerError.
        ecr_repo.add_lifecycle_rule(
            max_image_age=cdk.Duration.days(30),
            rule_priority=1,
            tag_status=ecr.TagStatus.UNTAGGED,
        )
        ecr_repo.add_lifecycle_rule(
            max_image_count=20, rule_priority=2, tag_status=ecr.TagStatus.ANY
        )

        cdk.CfnOutput(
            self, config.make_name("ECRRepositoryArn"), value=ecr_repo.repository_arn
        )
        cdk.CfnOutput(
            self, config.make_name("ECRRepositoryUri"), value=ecr_repo.repository_uri
        )
        return ecr_repo
