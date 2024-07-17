#!/usr/bin/env python3

import aws_cdk as cdk
from dotenv import load_dotenv

from ocs_deploy.config import OCSConfig
from ocs_deploy.ocs_deploy_stack import OcsDeployStack

load_dotenv(".env")

config = OCSConfig()

app = cdk.App()

OcsDeployStack(app, config)

app.synth()
