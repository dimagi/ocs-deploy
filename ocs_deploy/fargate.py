import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

from ocs_deploy.config import OCSConfig


class FargateStack(cdk.Stack):
    """
    Represents a CDK stack for deploying a Fargate service within a VPC.
     *
     * This stack sets up the necessary AWS resources to deploy a containerized
     * application using AWS Fargate. It includes setting up an ECS cluster,
     * task definitions, security groups, and an Application Load Balancer.
     * The stack also configures auto-scaling for the Fargate service based on CPU utilization.
    """

    def __init__(self, scope: Construct, vpc, ecr_repo, config: OCSConfig) -> None:
        super().__init__(scope, config.stack_name("Fargate"), env=config.env())

        self.fargate_service = self.setup_fargate_service(vpc, ecr_repo, config)

    def setup_fargate_service(self, vpc, ecr_repo, config: OCSConfig):
        http_sg = ec2.SecurityGroup(
            self, config.make_name("HttpSG"), vpc=vpc, allow_all_outbound=True
        )
        http_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80))

        https_sg = ec2.SecurityGroup(
            self, config.make_name("HttpsSG"), vpc=vpc, allow_all_outbound=True
        )
        https_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443))

        container_port = 8000

        # define a cluster with spot instances, linux type
        cluster = ecs.Cluster(
            self,
            config.make_name("DeploymentCluster"),
            vpc=vpc,
            container_insights=True,
            cluster_name=config.make_name("Cluster"),
        )

        # Task Role
        task_role = iam.Role(
            self,
            "ecsTaskExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        # Add permissions to the Task Role
        task_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AmazonECSTaskExecutionRolePolicy"
            )
        )

        # Add permissions to the Task Role to allow it to pull images from ECR
        task_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=["*"],
            )
        )

        # create a task definition with CloudWatch Logs
        log_driver = ecs.AwsLogDriver(stream_prefix=config.make_name())

        django_secret_key = secretsmanager.Secret(
            self,
            config.django_secret_key_secrets_name,
            secret_name=config.django_secret_key_secrets_name,
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=50,
            ),
        )

        # Instantiate Fargate Service with just cluster and image
        fargate_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            config.make_name("FargateService"),
            cluster=cluster,
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=ecs.ContainerImage.from_ecr_repository(ecr_repo, tag="latest"),
                container_name="web",
                task_role=task_role,
                container_port=container_port,
                environment={
                    "ACCOUNT_EMAIL_VERIFICATION": "mandatory",
                    "AWS_PRIVATE_STORAGE_BUCKET_NAME": config.s3_private_bucket_name,
                    "AWS_PUBLIC_STORAGE_BUCKET_NAME": config.s3_public_bucket_name,
                    "AWS_S3_REGION": config.region,
                    "AZURE_REGION": config.azure_region,
                    "DJANGO_EMAIL_BACKEND": config.django_email_backend,
                    "DJANGO_SETTINGS_MODULE": "gpt_playground.settings_production",
                    "PRIVACY_POLICY_URL": config.privacy_policy_url,
                    "TERMS_URL": config.terms_url,
                    "SIGNUP_ENABLED": config.signup_enabled,
                    "SLACK_BOT_NAME": config.slack_bot_name,
                    "USE_S3_STORAGE": config.use_s3_storage,
                    "WHATSAPP_S3_AUDIO_BUCKET": config.whatsapp_s3_audio_bucket,
                },
                enable_logging=True,
                log_driver=log_driver,
                secrets={
                    "DATABASE_URL": ecs.Secret.from_secrets_manager(
                        config.rds_url_secrets_name
                    ),
                    "REDIS_URL": ecs.Secret.from_secrets_manager(
                        config.redis_url_secrets_name
                    ),
                    "SECRET_KEY": django_secret_key,
                    # "AWS_SECRET_ACCESS_KEY": ecs.Secret.from_secrets_manager(TODO)
                    # "AWS_SES_ACCESS_KEY": ecs.Secret.from_secrets_manager(TODO)
                    # "AWS_SES_REGION": ecs.Secret.from_secrets_manager(TODO)
                    # "AWS_SES_SECRET_KEY": ecs.Secret.from_secrets_manager(TODO)
                    # "AZURE_SUBSCRIPTION_KEY": ecs.Secret.from_secrets_manager(TODO)
                    # "CRYPTOGRAPHY_SALT": ecs.Secret.from_secrets_manager(TODO)
                    # "OPENAI_API_KEY": ecs.Secret.from_secrets_manager(TODO)
                    # "SENTRY_DSN": ecs.Secret.from_secrets_manager(TODO)
                    # "SLACK_CLIENT_ID": ecs.Secret.from_secrets_manager(TODO)
                    # "SLACK_CLIENT_SECRET": ecs.Secret.from_secrets_manager(TODO)
                    # "SLACK_SIGNING_SECRET": ecs.Secret.from_secrets_manager(TODO)
                    # "TASKBADGER_API_KEY": ecs.Secret.from_secrets_manager(TODO)
                    # "TASKBADGER_ORG": ecs.Secret.from_secrets_manager(TODO)
                    # "TASKBADGER_PROJECT": ecs.Secret.from_secrets_manager(TODO)
                    # "TELEGRAM_SECRET_TOKEN": ecs.Secret.from_secrets_manager(TODO)
                },
            ),
            security_groups=[http_sg, https_sg],
            cpu=256,
            memory_limit_mib=512,
            desired_count=1,
            public_load_balancer=True,
            load_balancer_name=config.make_name("LoadBalancer"),
            service_name=config.make_name("Django"),
            # // certificate: acm.Certificate.fromCertificateArn(this, `${props.appName}-${props.environment}-FargateServiceCertificate`, props.certificateArn),
            # // certificate,
            # // redirectHTTP: true,
            # // protocol: cdk.aws_elasticloadbalancingv2.ApplicationProtocol.HTTPS,
            # // protocolVersion: cdk.aws_elasticloadbalancingv2.ApplicationProtocolVersion.HTTP1,
        )

        # Setup AutoScaling policy
        scaling = fargate_service.service.auto_scale_task_count(
            max_capacity=2, min_capacity=1
        )
        scaling.scale_on_cpu_utilization(
            config.make_name("CpuScaling"),
            target_utilization_percent=70,
            scale_in_cooldown=cdk.Duration.seconds(60),
            scale_out_cooldown=cdk.Duration.seconds(60),
        )

        # print out fargateService load balancer url
        cdk.CfnOutput(
            self,
            config.make_name("FargateServiceLoadBalancerDNS"),
            value=fargate_service.load_balancer.load_balancer_dns_name,
        )
        return fargate_service
