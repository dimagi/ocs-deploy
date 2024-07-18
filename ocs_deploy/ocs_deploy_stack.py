from aws_cdk import (
    Stack,
)
from constructs import Construct

from ocs_deploy.ecr import EcrStack
from ocs_deploy.fargate import FargateStack
from ocs_deploy.vpc import VpcStack

from ocs_deploy.config import OCSConfig


class OcsInfraSetupStack(Stack):
    def __init__(self, scope: Construct, config: OCSConfig) -> None:
        super().__init__(scope, config.stack_name("infra"), env=config.env())

        self.ecr_repo = EcrStack(self, config).repo
        self.vpc = VpcStack(self, config).vpc


class OcsServicesStack(Stack):
    def __init__(self, scope: Construct, vpc, ecr_repo, config: OCSConfig) -> None:
        super().__init__(scope, config.stack_name("services"), env=config.env())
        self.fargate_service = FargateStack(self, vpc, ecr_repo, config)
