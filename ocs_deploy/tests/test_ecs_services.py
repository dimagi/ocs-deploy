"""`OCSConfig.ecs_services` is the source of truth for every service enumeration.

These tests hold it to what the stacks actually synthesize, so adding a service
can't leave the deploy role's IAM policy or the CLI's `--services` list behind.
"""

import aws_cdk as cdk
from aws_cdk.assertions import Template

from ocs_deploy.domains import DomainStack
from ocs_deploy.ecr import EcrStack
from ocs_deploy.fargate import FargateStack
from ocs_deploy.github import GithubOidcStack
from ocs_deploy.rds import RdsStack
from ocs_deploy.redis import RedisStack
from ocs_deploy.ses_inbound import SesInboundStack
from ocs_deploy.vpc import VpcStack


def _fargate_template(app, config):
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
    return Template.from_stack(fargate)


def test_config_lists_every_synthesized_service(ocs_config):
    template = _fargate_template(cdk.App(), ocs_config)
    synthesized = {
        props["Properties"]["ServiceName"]
        for props in template.find_resources("AWS::ECS::Service").values()
    }
    configured = {name for name, _ in ocs_config.ecs_services.values()}
    assert configured == synthesized


def test_config_container_names_match_task_definitions(ocs_config):
    template = _fargate_template(cdk.App(), ocs_config)
    synthesized = {
        container["Name"]
        for props in template.find_resources("AWS::ECS::TaskDefinition").values()
        for container in props["Properties"]["ContainerDefinitions"]
    }
    for _, container_name in ocs_config.ecs_services.values():
        assert container_name in synthesized


def test_deploy_role_can_update_every_service(ocs_config):
    """The github_deploy role needs ecs:UpdateService/DescribeServices on all of them."""
    template = Template.from_stack(GithubOidcStack(cdk.App(), ocs_config))
    policies = template.find_resources("AWS::IAM::Policy")
    statements = [
        statement
        for props in policies.values()
        for statement in props["Properties"]["PolicyDocument"]["Statement"]
    ]
    deploy = next(s for s in statements if s.get("Sid") == "DeployService")
    granted = set(deploy["Resource"])
    for service_name, _ in ocs_config.ecs_services.values():
        assert any(r.endswith(f"/{service_name}") for r in granted), service_name
