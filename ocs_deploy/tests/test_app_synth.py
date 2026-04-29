import aws_cdk as cdk

from ocs_deploy.domains import DomainStack
from ocs_deploy.ecr import EcrStack
from ocs_deploy.fargate import FargateStack
from ocs_deploy.rds import RdsStack
from ocs_deploy.redis import RedisStack
from ocs_deploy.ses_inbound import SesInboundStack
from ocs_deploy.vpc import VpcStack


def test_app_can_synth_with_ses_inbound(ocs_config):
    app = cdk.App()
    config = ocs_config

    domain_stack = DomainStack(app, config)
    vpc_stack = VpcStack(app, config)
    ecr_stack = EcrStack(app, config)
    rds_stack = RdsStack(app, vpc_stack.vpc, config)
    redis_stack = RedisStack(app, vpc_stack.vpc, config)
    ses_inbound_stack = SesInboundStack(app, config)
    fargate = FargateStack(
        app,
        vpc_stack.vpc,
        ecr_stack.repo,
        rds_stack,
        redis_stack,
        domain_stack,
        ses_inbound_stack,
        config,
    )
    fargate.add_dependency(ses_inbound_stack)

    # Should not raise.
    app.synth()
