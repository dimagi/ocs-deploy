import os

import aws_cdk as cdk


class OCSConfig:
    account: str
    app_name: str
    environment: str
    region: str
    maintenance_window: str

    django_email_backend: str
    azure_region: str
    privacy_policy_url: str
    terms_url: str
    signup_enabled: str
    slack_bot_name: str

    def __init__(self, config: dict = None):
        config = config.get if config else os.getenv
        self.account = config("CDK_ACCOUNT")
        self.app_name = config("APP_NAME", "open-chat-studio")
        self.environment = config("ENVIRONMENT", "dev")
        self.region = config("CDK_REGION")
        self.maintenance_window = config("MAINTENANCE_WINDOW", "Mon:00:00-Mon:03:00")

        self.azure_region = config("AZURE_REGION", "eastus")
        self.django_email_backend = config("DJANGO_EMAIL_BACKEND")
        self.privacy_policy_url = config("PRIVACY_POLICY_URL")
        self.terms_url = config("TERMS_URL")
        self.signup_enabled = config("SIGNUP_ENABLED")
        self.slack_bot_name = config("SLACK_BOT_NAME")

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
    def ecr_repo_name(self):
        return self.make_name("EcrRepo")

    @property
    def rds_url_secrets_name(self):
        return self.make_name("RdsDatabaseUrl")

    @property
    def redis_url_secrets_name(self):
        return self.make_name("RedisUrl")

    @property
    def django_secret_key_secrets_name(self):
        return self.make_name("DjangoSecretKey")

    # TODO: create buckets
    @property
    def s3_private_bucket_name(self):
        return self.make_name("PrivateBucket")

    @property
    def s3_public_bucket_name(self):
        return self.make_name("PublicBucket")

    @property
    def whatsapp_s3_audio_bucket(self):
        return self.make_name("WhatsappS3Audio")
