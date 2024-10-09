#!/usr/bin/env python3

import aws_cdk as cdk
from dotenv import load_dotenv

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

load_dotenv(".env")

config = OCSConfig()

app = cdk.App()

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
ocs_services.add_dependency(vpc_stack)
ocs_services.add_dependency(ecr_stack)
ocs_services.add_dependency(rds_stack)
ocs_services.add_dependency(redis_stack)

app.synth()
