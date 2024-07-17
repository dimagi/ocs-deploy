import os

import aws_cdk as cdk


class OCSConfig:
    account: str
    app_name: str
    environment: str
    region: str
    ecr_repo_name: str

    def __init__(self):
        self.account = os.getenv("CDK_ACCOUNT")
        self.app_name = os.getenv("APP_NAME", "open-chat-studio")
        self.environment = os.getenv("ENVIRONMENT", "dev")
        self.region = os.getenv("CDK_REGION")
        self.ecr_repo_name = os.getenv("ECR_REPO_NAME")

    def stack_name(self, name: str):
        return self.make_name(name, include_region=True)

    def env(self):
        return cdk.Environment(account=os.getenv("CDK_ACCOUNT"), region=self.region)

    def make_name(self, name: str = "", include_region=False):
        name = f"-{name}" if name else ""
        if include_region:
            return f"{self.app_name}-{self.environment}-{self.region}{name}"
        return f"{self.app_name}-{self.environment}{name}"
