#!/usr/bin/env python3

import aws_cdk as cdk
from dotenv import load_dotenv

from ocs_deploy.config import OCSConfig
from ocs_deploy.ecr import EcrStack
from ocs_deploy.fargate import FargateStack
from ocs_deploy.rds import RdsStack
from ocs_deploy.vpc import VpcStack

load_dotenv(".env")

config = OCSConfig()

app = cdk.App()

vpc = VpcStack(app, config)
ecr = EcrStack(app, config)

rds = RdsStack(app, vpc.vpc, config)
rds.add_dependency(vpc)

redis = RdsStack(app, vpc.vpc, config)
redis.add_dependency(vpc)

ocs_services = FargateStack(app, vpc.vpc, ecr.repo, config)
ocs_services.add_dependency(vpc)
ocs_services.add_dependency(ecr)
ocs_services.add_dependency(redis)

app.synth()
