import aws_cdk as cdk
from aws_cdk import (
    NestedStack,
    aws_ecr as ecr,
)
from constructs import Construct

from ocs_deploy.config import OCSConfig


class EcrStack(NestedStack):
    def __init__(self, scope: Construct, config: OCSConfig) -> None:
        super().__init__(scope, config.stack_name("ECR"))
        self.repo = self.setup_ecr(config)

    def setup_ecr(self, config: OCSConfig):
        ecr_repo = ecr.Repository(
            self, config.make_name("ECR"), repository_name=config.ecr_repo_name
        )
        ecr_repo.add_lifecycle_rule(
            max_image_age=cdk.Duration.days(7),
            rule_priority=1,
            tag_status=ecr.TagStatus.UNTAGGED,
        )
        ecr_repo.add_lifecycle_rule(
            max_image_count=4, rule_priority=2, tag_status=ecr.TagStatus.ANY
        )

        cdk.CfnOutput(
            self, config.make_name("ECRRepositoryArn"), value=ecr_repo.repository_arn
        )
        cdk.CfnOutput(
            self, config.make_name("ECRRepositoryName"), value=ecr_repo.repository_name
        )
        return ecr_repo
