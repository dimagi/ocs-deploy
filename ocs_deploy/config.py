import os
import re

import aws_cdk as cdk


class OCSConfig:
    VPC_STACK = "vpc"
    ECR_STACK = "ecr"
    RDS_STACK = "rds"
    REDIS_STACK = "redis"
    DJANGO_STACK = "django"

    ALL_STACKS = [
        VPC_STACK,
        ECR_STACK,
        RDS_STACK,
        REDIS_STACK,
        DJANGO_STACK,
    ]

    def __init__(self, config: dict = None):
        config = config if config else os.environ
        self.account = config["CDK_ACCOUNT"]
        self.region = config["CDK_REGION"]

        self.django_email_backend = config["DJANGO_EMAIL_BACKEND"]

        self.app_name = config.get("APP_NAME", "ocs")
        self.environment = config.get("ENVIRONMENT", "dev")
        self.maintenance_window = config.get(
            "MAINTENANCE_WINDOW", "Mon:00:00-Mon:03:00"
        )

        self.azure_region = config.get("AZURE_REGION", "eastus")
        self.privacy_policy_url = config.get("PRIVACY_POLICY_URL", "")
        self.terms_url = config.get("TERMS_URL", "")
        self.signup_enabled = config.get("SIGNUP_ENABLED", "False")
        self.slack_bot_name = config.get("SLACK_BOT_NAME", "OCS Bot")

    def stack_name(self, name: str):
        if name not in self.ALL_STACKS:
            raise Exception(f"Invalid stack name: {name}")
        return self.make_name(f"{name}-stack", include_region=True)

    def env(self):
        return cdk.Environment(account=os.getenv("CDK_ACCOUNT"), region=self.region)

    def make_name(self, name: str = "", include_region=False):
        name = f"-{name}" if name else ""
        if include_region:
            return f"{self.app_name}-{self.environment}-{self.region}{name}"
        return f"{self.app_name}-{self.environment}{name}"

    def make_secret_name(self, name: str):
        if re.match(r"-[a-zA-Z]{6}$", name):
            raise Exception(
                "Secret name should not end with a hyphen and 6 characters."
                "See https://docs.aws.amazon.com/secretsmanager/latest/userguide/troubleshoot.html#ARN_secretnamehyphen"
            )
        return f"{self.app_name}/{self.environment}/{name}"

    @property
    def rds_db_name(self):
        return self.make_name("ocs-db")

    @property
    def ecr_repo_name(self):
        return self.make_name("ecr-repo")

    @property
    def rds_url_secrets_name(self):
        return self.make_secret_name("rds-db-url")

    @property
    def redis_url_secrets_name(self):
        return self.make_secret_name("redis-url")

    @property
    def django_secret_key_secrets_name(self):
        return self.make_secret_name("django-secret-key")

    # TODO: create buckets
    @property
    def s3_private_bucket_name(self):
        return self.make_name("s3-private")

    @property
    def s3_public_bucket_name(self):
        return self.make_name("s3-public")

    @property
    def whatsapp_s3_audio_bucket(self):
        return self.make_name("s3-whatsapp-audio")
