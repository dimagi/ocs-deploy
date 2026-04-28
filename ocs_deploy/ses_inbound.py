import aws_cdk as cdk
from aws_cdk import (
    aws_iam as iam,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
    aws_ses as ses,
    aws_ses_actions as ses_actions,
    aws_sns as sns,
    aws_sns_subscriptions as sns_subs,
)
from constructs import Construct

from ocs_deploy.config import OCSConfig

INBOUND_PREFIX = "inbound/"


class SesInboundStack(cdk.Stack):
    """SES inbound mail plumbing: S3 bucket, SNS topic, receipt rules, webhook secret."""

    def __init__(self, scope: Construct, config: OCSConfig) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.SES_INBOUND_STACK), env=config.cdk_env()
        )
        self.config = config
        self.bucket = self._create_bucket()
        self.topic = self._create_topic()
        self.webhook_secret = self._create_webhook_secret()
        self.rule_set, self.rule = self._create_receipt_rules()
        self._add_webhook_subscription()

    def _create_bucket(self) -> s3.Bucket:
        bucket = s3.Bucket(
            self,
            self.config.make_name("SesInboundBucket"),
            bucket_name=self.config.make_name("ses-inbound-mail"),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    enabled=True,
                    prefix=INBOUND_PREFIX,
                    expiration=cdk.Duration.days(7),
                )
            ],
        )
        bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowSESPuts",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("ses.amazonaws.com")],
                actions=["s3:PutObject"],
                resources=[f"{bucket.bucket_arn}/{INBOUND_PREFIX}*"],
                conditions={
                    "StringEquals": {
                        "aws:SourceAccount": self.config.account,
                        "aws:SourceArn": (
                            f"arn:aws:ses:{self.config.region}"
                            f":{self.config.account}"
                            f":receipt-rule-set/{self.config.make_name('inbound')}"
                            f":receipt-rule/*"
                        ),
                    },
                },
            )
        )
        cdk.CfnOutput(
            self,
            self.config.make_name("SesInboundBucketName"),
            value=bucket.bucket_name,
        )
        return bucket

    def _create_topic(self) -> sns.Topic:
        topic = sns.Topic(
            self,
            self.config.make_name("SesInboundTopic"),
            topic_name=self.config.make_name("ses-inbound"),
            display_name="OCS SES Inbound",
        )
        cdk.CfnOutput(
            self,
            self.config.make_name("SesInboundTopicArn"),
            value=topic.topic_arn,
        )
        return topic

    def _create_webhook_secret(self) -> secretsmanager.Secret:
        return secretsmanager.Secret(
            self,
            self.config.make_name("AnymailWebhookSecret"),
            secret_name=self.config.anymail_webhook_secret_name,
            description="Basic-auth value used by anymail's SES inbound webhook.",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=32,
                exclude_characters=":/@\"' \\?#%[]",
                exclude_punctuation=False,
            ),
        )

    def _create_receipt_rules(self) -> tuple[ses.ReceiptRuleSet, ses.ReceiptRule]:
        rule_set = ses.ReceiptRuleSet(
            self,
            self.config.make_name("SesInboundRuleSet"),
            receipt_rule_set_name=self.config.make_name("inbound"),
        )
        rule = rule_set.add_rule(
            self.config.make_name("DeliverInboundMail"),
            recipients=self.config.all_inbound_domains,
            scan_enabled=True,
            enabled=True,
            actions=[
                ses_actions.S3(
                    bucket=self.bucket,
                    object_key_prefix=INBOUND_PREFIX,
                ),
                ses_actions.Sns(
                    topic=self.topic,
                    encoding=ses_actions.EmailEncoding.BASE64,
                ),
            ],
        )

        cdk.CfnOutput(
            self,
            self.config.make_name("SesInboundRuleSetName"),
            value=rule_set.receipt_rule_set_name,
        )
        cdk.CfnOutput(
            self,
            self.config.make_name("ActivateReceiptRuleSetCommand"),
            value=(
                f"aws ses set-active-receipt-rule-set "
                f"--rule-set-name {rule_set.receipt_rule_set_name}"
            ),
            description="Run this once after deploy to make the rule set active.",
        )
        return rule_set, rule

    def _add_webhook_subscription(self) -> None:
        secret_value = cdk.SecretValue.secrets_manager(
            self.config.anymail_webhook_secret_name
        ).unsafe_unwrap()
        endpoint = (
            f"https://anymail:{secret_value}@"
            f"{self.config.domain_name}/anymail/amazon_ses/inbound/"
        )
        self.topic.add_subscription(
            sns_subs.UrlSubscription(endpoint, protocol=sns.SubscriptionProtocol.HTTPS)
        )
