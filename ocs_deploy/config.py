import dataclasses
import re
from datetime import datetime
from pathlib import Path

import boto3
import yaml
from dotenv import dotenv_values


class OCSConfig:
    GITHUB_STACK = "github"
    EC2_TMP_STACK = "ec2tmp"
    DOMAINS_STACK = "domains"
    S3_STACK = "s3"
    VPC_STACK = "vpc"
    ECR_STACK = "ecr"
    RDS_STACK = "rds"
    REDIS_STACK = "redis"
    DJANGO_STACK = "django"
    WAF_STACK = "waf"
    GUARD_DUTY_STACK = "guardduty"
    SECURITYHUB_STACK = "securityhub"
    DETECTIVE_STACK = "detective"

    ALL_STACKS = [
        GITHUB_STACK,
        EC2_TMP_STACK,
        DOMAINS_STACK,
        S3_STACK,
        VPC_STACK,
        ECR_STACK,
        RDS_STACK,
        REDIS_STACK,
        DJANGO_STACK,
        WAF_STACK,
        GUARD_DUTY_STACK,
        SECURITYHUB_STACK,
        DETECTIVE_STACK,
    ]

    LOG_GROUP_DJANGO = "DjangoLogs"
    LOG_GROUP_CELERY = "CeleryWorkerLogs"
    LOG_GROUP_BEAT = "CeleryBeatLogs"

    CONTAINER_PORT = 8000

    def __init__(self, env: str):
        if not env:
            raise Exception("No environment specified")

        env_path = Path(f".env.{env}")
        if not env_path.exists():
            raise Exception(f"Environment file not found: {env_path}")

        config = dotenv_values(env_path)
        self.environment = env
        self.account = config["CDK_ACCOUNT"]
        self.region = config["CDK_REGION"]

        self.email_domain = config["EMAIL_DOMAIN"]
        self.domain_name = config["DOMAIN_NAME"]

        self.app_name = config.get("APP_NAME", "ocs")
        self.maintenance_window = config.get(
            "MAINTENANCE_WINDOW", "Mon:00:00-Mon:03:00"
        )

        self.privacy_policy_url = config.get("PRIVACY_POLICY_URL", "")
        self.terms_url = config.get("TERMS_URL", "")
        self.signup_enabled = config.get("SIGNUP_ENABLED", "False")
        self.slack_bot_name = config.get("SLACK_BOT_NAME", "OCS Bot")

        self.taskbadger_org = config.get("TASKBADGER_ORG", "")
        self.taskbadger_project = config.get("TASKBADGER_PROJECT", "")
        self.sentry_environment = config.get("SENTRY_ENVIRONMENT", "development")

        self.github_repo = config.get("GITHUB_REPO", "dimagi/open-chat-studio")
        self.allowed_hosts = config["DJANGO_ALLOWED_HOSTS"]
        self.django_server_email = config.get("DJANGO_SERVER_EMAIL", "")
        self.django_default_from_email = config.get("DJANGO_DEFAULT_FROM_EMAIL", "")

    def stack_name(self, name: str):
        if name not in self.ALL_STACKS:
            raise Exception(f"Invalid stack name: {name}")
        return self.make_name(f"{name}-stack", include_region=True)

    def cdk_env(self):
        import aws_cdk as cdk

        return cdk.Environment(account=self.account, region=self.region)

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
        """Name of the RDS database.
        Must start with a letter and contain only alphanumeric characters."""

        name = re.sub(r"[^a-zA-Z0-9]", "", self.app_name).lower()
        if not name:
            raise Exception("Invalid RDS database name")
        return name

    @property
    def ecs_cluster_name(self):
        return self.make_name("Cluster")

    @property
    def ecs_django_service_name(self):
        return self.make_name("Django")

    @property
    def ecs_celery_service_name(self):
        return self.make_name("Celery")

    @property
    def ecs_celery_beat_service_name(self):
        return self.make_name("CeleryBeat")

    @property
    def ecr_repo_name(self):
        return self.make_name("ecr-repo")

    @property
    def ecs_task_role_name(self):
        return self.make_name("ecs-task-role")

    @property
    def ecs_task_execution_role(self):
        return self.make_name("ecs-task-execution-role")

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
    def s3_whatsapp_audio_bucket(self):
        return self.make_name("s3-whatsapp-audio")

    def get_django_env(self, rds_host, rds_port):
        env_dict = {
            "ACCOUNT_EMAIL_VERIFICATION": "mandatory",
            "AWS_PRIVATE_STORAGE_BUCKET_NAME": self.s3_private_bucket_name,
            "AWS_PUBLIC_STORAGE_BUCKET_NAME": self.s3_public_bucket_name,
            "AWS_S3_REGION": self.region,
            "DJANGO_DATABASE_NAME": self.rds_db_name,
            "DJANGO_DATABASE_HOST": rds_host,
            "DJANGO_DATABASE_PORT": rds_port,
            "DJANGO_EMAIL_BACKEND": "anymail.backends.amazon_ses.EmailBackend",
            "DJANGO_SECURE_SSL_REDIRECT": "false",  # handled by the load balancer
            "DJANGO_SETTINGS_MODULE": "gpt_playground.settings_production",
            "PORT": str(self.CONTAINER_PORT),
            "PRIVACY_POLICY_URL": self.privacy_policy_url,
            "TERMS_URL": self.terms_url,
            "SIGNUP_ENABLED": self.signup_enabled,
            "SLACK_BOT_NAME": self.slack_bot_name,
            "USE_S3_STORAGE": "True",
            "WHATSAPP_S3_AUDIO_BUCKET": self.s3_whatsapp_audio_bucket,
            "TASKBADGER_ORG": self.taskbadger_org,
            "TASKBADGER_PROJECT": self.taskbadger_project,
            "SENTRY_ENVIRONMENT": self.sentry_environment,
            "DJANGO_ALLOWED_HOSTS": self.allowed_hosts,
        }
        if self.django_server_email:
            env_dict["DJANGO_SERVER_EMAIL"] = self.django_server_email
        if self.django_default_from_email:
            env_dict["DJANGO_DEFAULT_FROM_EMAIL"] = self.django_default_from_email
        return env_dict

    def normalize_secret_name(self, name):
        prefix = self.make_secret_name("")
        if not name.startswith(prefix):
            name = self.make_secret_name(name)
        return name

    def get_secret(self, name):
        name = self.normalize_secret_name(name)
        found = [secret for secret in self.get_secrets_list() if secret.name == name]
        if not found:
            raise ValueError(f"Secret not found: {name}")
        return found[0]

    def get_secrets_list(self):
        path = Path(__file__).parent / "secrets.yml"
        with path.open() as f:
            data = yaml.safe_load(f)
        return [
            Secret(
                name=self.make_secret_name(raw["name"]),
                managed=raw.get("managed", False),
                optional=raw.get("optional", False),
            )
            for raw in data["secrets"]
        ]

    def _secret_exists(self, secret_name: str) -> bool:
        """Check if a secret exists in AWS Secrets Manager."""
        try:
            client = boto3.client("secretsmanager", region_name=self.region)
            client.describe_secret(SecretId=secret_name)
            return True
        except client.exceptions.ResourceNotFoundException:
            return False
        except Exception as e:
            # For other errors (permissions, etc), log and return False
            print(f"Warning: Could not check secret {secret_name}: {e}")
            return True  # assume it exists, this will fail cause the deploy to fail if it doesn't exist

    def get_existing_secrets_list(self):
        """Return only secrets that exist in AWS Secrets Manager.

        Skips managed secrets and optional secrets that don't exist.
        """
        secrets = []
        for secret in self.get_secrets_list():
            if secret.managed:
                continue

            # For optional secrets, only include if they exist in AWS Secrets Manager
            if secret.optional:
                if not self._secret_exists(secret.name):
                    print(
                        f"Skipping optional secret {secret.name} (not found in Secrets Manager)"
                    )
                    continue

            secrets.append(secret)
        return secrets


@dataclasses.dataclass
class Secret:
    name: str
    arn: str = ""
    created: datetime | None = None
    last_accessed: datetime | None = None
    last_changed: datetime | None = None
    value: str = ""
    managed: bool = False
    optional: bool = False

    @classmethod
    def from_dict(cls, data):
        created = data.get("CreatedDate")
        accessed = data.get("LastAccessedDate")
        changed = data.get("LastChangedDate")
        return cls(
            arn=data["ARN"],
            name=data["Name"],
            created=datetime.fromisoformat(created) if created else None,
            last_accessed=datetime.fromisoformat(accessed) if accessed else None,
            last_changed=datetime.fromisoformat(changed) if changed else None,
            value=data.get("SecretString"),
        )

    def table_row(self):
        return [
            self.name,
            self.created.ctime() if self.created else "",
            self.last_accessed.ctime() if self.last_accessed else "",
            self.last_changed.ctime() if self.last_changed else "",
        ]

    def __str__(self):
        return f"{self.name}"

    @property
    def env_var(self):
        return self.name.split("/")[-1].upper()
