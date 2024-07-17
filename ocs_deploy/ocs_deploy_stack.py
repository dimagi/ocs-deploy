from aws_cdk import (
    Stack,
)
from constructs import Construct

from ocs_deploy.ecr import EcrStack
from ocs_deploy.fargate import FargateStack
from ocs_deploy.vpc import VpcStack

from ocs_deploy.config import OCSConfig


class OcsDeployStack(Stack):
    def __init__(self, scope: Construct, config: OCSConfig) -> None:
        super().__init__(scope, config.stack_name("OCSDeployStack"), env=config.env())

        ecr_stack = EcrStack(self, config)
        vpc_stack = VpcStack(self, config)

        FargateStack(self, vpc_stack.vpc, ecr_stack.repo, config)
