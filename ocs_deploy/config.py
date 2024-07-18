import os

import aws_cdk as cdk


class OCSConfig:
    account: str
    app_name: str
    environment: str
    region: str
    ecr_repo_name: str
    rds_username: str
    rds_database_name: str
    maintenance_window: str

    def __init__(self, config: dict = None):
        config = config.get if config else os.getenv
        self.account = config("CDK_ACCOUNT")
        self.app_name = config("APP_NAME", "open-chat-studio")
        self.environment = config("ENVIRONMENT", "dev")
        self.region = config("CDK_REGION")
        self.ecr_repo_name = config("ECR_REPO_NAME")
        self.rds_username = config("RDS_USERNAME")
        self.rds_database_name = config("RDS_DATABASE_NAME")
        self.maintenance_window = config("MAINTENANCE_WINDOW", "Mon:00:00-Mon:03:00")

    def stack_name(self, name: str):
        return self.make_name(name, include_region=True)

    def env(self):
        return cdk.Environment(account=os.getenv("CDK_ACCOUNT"), region=self.region)

    def make_name(self, name: str = "", include_region=False):
        name = f"-{name}" if name else ""
        if include_region:
            return f"{self.app_name}-{self.environment}-{self.region}{name}"
        return f"{self.app_name}-{self.environment}{name}"

    @property
    def rds_url_secrets_name(self):
        return self.make_name("RdsDatabaseUrl")

    @property
    def redis_url_secrets_name(self):
        return self.make_name("RedisUrl")

    @property
    def django_secret_key_secrets_name(self):
        return self.make_name("DjangoSecretKey")
