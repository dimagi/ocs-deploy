#!/usr/bin/env python3
import os

import aws_cdk as cdk
from dotenv import load_dotenv

from ocs_deploy.ocs_deploy_stack import OcsDeployStack

load_dotenv(".env")

app = cdk.App()
OcsDeployStack(
    app,
    "OcsDeployStack",
    env=cdk.Environment(
        account=os.getenv("CDK_ACCOUNT"), region=os.getenv("CDK_REGION")
    ),
)

app.synth()
