#!/usr/bin/env python3

import aws_cdk as cdk
from dotenv import load_dotenv

from ocs_deploy.config import OCSConfig
from ocs_deploy.ocs_deploy_stack import OcsServicesStack, OcsInfraSetupStack

load_dotenv(".env")

config = OCSConfig()

app = cdk.App()

infra = OcsInfraSetupStack(app, config)

ocs_services = OcsServicesStack(app, infra.vpc, infra.ecr_repo, config)
ocs_services.add_dependency(infra)

app.synth()
