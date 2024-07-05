#!/usr/bin/env python3
import os

import aws_cdk as cdk
from dotenv import load_dotenv

from ocs_deploy.ocs_deploy_stack import OcsDeployStack

load_dotenv(".env")

app_name = os.getenv("APP_NAME", "open-chat-studio")
environment = os.getenv("ENVIRONMENT", "dev")
cdk_region = os.getenv("CDK_REGION")

app = cdk.App()
OcsDeployStack(
    app,
    f"{app_name}-{environment}-{cdk_region}-OcsDeployStack`",
    env=cdk.Environment(account=os.getenv("CDK_ACCOUNT"), region=cdk_region),
)

app.synth()
