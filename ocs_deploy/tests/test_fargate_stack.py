import aws_cdk as cdk
import aws_cdk.assertions as assertions

from ocs_deploy.domains import DomainStack
from ocs_deploy.ec2_tmp import Ec2TmpStack
from ocs_deploy.ecr import EcrStack
from ocs_deploy.fargate import FargateStack
from ocs_deploy.rds import RdsStack
from ocs_deploy.redis import RedisStack
from ocs_deploy.ses_inbound import SesInboundStack
from ocs_deploy.vpc import VpcStack


def _synth_fargate(config):
    app = cdk.App()
    domain_stack = DomainStack(app, config)
    vpc_stack = VpcStack(app, config)
    Ec2TmpStack(app, vpc_stack.vpc, config)
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
    return assertions.Template.from_stack(fargate)


def test_task_role_has_sns_confirm_subscription(ocs_config):
    template = _synth_fargate(ocs_config)
    template.has_resource_properties(
        "AWS::IAM::Policy",
        assertions.Match.object_like(
            {
                "PolicyDocument": {
                    "Statement": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {
                                    "Action": "sns:ConfirmSubscription",
                                    "Effect": "Allow",
                                }
                            )
                        ]
                    )
                }
            }
        ),
    )


def test_task_role_has_s3_getobject_on_inbound_prefix(ocs_config):
    template = _synth_fargate(ocs_config)
    template.has_resource_properties(
        "AWS::IAM::Policy",
        assertions.Match.object_like(
            {
                "PolicyDocument": {
                    "Statement": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {
                                    "Action": "s3:GetObject",
                                    "Effect": "Allow",
                                }
                            )
                        ]
                    )
                }
            }
        ),
    )


def test_anymail_webhook_secret_in_task_definition(ocs_config):
    template = _synth_fargate(ocs_config)
    matches = template.find_resources(
        "AWS::ECS::TaskDefinition",
        assertions.Match.object_like(
            {
                "Properties": {
                    "ContainerDefinitions": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {
                                    "Secrets": assertions.Match.array_with(
                                        [
                                            assertions.Match.object_like(
                                                {"Name": "ANYMAIL_WEBHOOK_SECRET"}
                                            )
                                        ]
                                    )
                                }
                            )
                        ]
                    )
                }
            }
        ),
    )
    # web + celery worker + celery beat + migration = 4 task defs include this secret.
    assert len(matches) == 4
