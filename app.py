#!/usr/bin/env python3

import aws_cdk as cdk

from ocs_deploy.config import OCSConfig
from ocs_deploy.domains import DomainStack
from ocs_deploy.ec2_tmp import Ec2TmpStack
from ocs_deploy.ecr import EcrStack
from ocs_deploy.fargate import FargateStack
from ocs_deploy.github import GithubOidcStack
from ocs_deploy.rds import RdsStack
from ocs_deploy.redis import RedisStack
from ocs_deploy.s3 import S3Stack
from ocs_deploy.vpc import VpcStack
from ocs_deploy.waf import WAFStack
from ocs_deploy.guardDuty import GuardDutyStack
from ocs_deploy.securityhub import SecurityHubStack
from ocs_deploy.detective import DetectiveStack

app = cdk.App()
env = app.node.try_get_context("ocs_env")
config = OCSConfig(env)

S3Stack(app, config)
GithubOidcStack(app, config)

domain_stack = DomainStack(app, config)
vpc_stack = VpcStack(app, config)

ec2_tmp_stack = Ec2TmpStack(app, vpc_stack.vpc, config)

ecr_stack = EcrStack(app, config)

rds_stack = RdsStack(app, vpc_stack.vpc, config)
rds_stack.add_dependency(vpc_stack)

redis_stack = RedisStack(app, vpc_stack.vpc, config)
redis_stack.add_dependency(vpc_stack)

ocs_services = FargateStack(
    app, vpc_stack.vpc, ecr_stack.repo, rds_stack, redis_stack, domain_stack, config
)
waf_stack = WAFStack(app, config, ocs_services.load_balancer_arn)
waf_stack.add_dependency(ocs_services)

guardduty_stack = GuardDutyStack(app, config)

securityhub_stack = SecurityHubStack(app, config)

detectiveStack = DetectiveStack(app, config)

ocs_services.add_dependency(vpc_stack)
ocs_services.add_dependency(ecr_stack)
ocs_services.add_dependency(rds_stack)
ocs_services.add_dependency(redis_stack)

app.synth()
