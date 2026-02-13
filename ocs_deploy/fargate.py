from functools import cached_property

import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_iam as iam,
    aws_logs as logs,
    aws_elasticloadbalancingv2 as elb,
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

    def __init__(
        self,
        scope: Construct,
        vpc,
        ecr_repo,
        rds_stack,
        redis_stack,
        domain_stack,
        config: OCSConfig,
    ) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.DJANGO_STACK), env=config.cdk_env()
        )

        self.config = config
        self.rds_stack = rds_stack
        self.redis_stack = redis_stack
        self.domain_stack = domain_stack

        self.fargate_service = self.setup_fargate_service(vpc, ecr_repo, config)
        # Expose ALB ARN for WAF
        self.load_balancer_arn = self.fargate_service.load_balancer.load_balancer_arn

    def setup_fargate_service(self, vpc, ecr_repo, config: OCSConfig):
        http_sg = ec2.SecurityGroup(
            self, config.make_name("HttpSG"), vpc=vpc, allow_all_outbound=True
        )
        http_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80))

        https_sg = ec2.SecurityGroup(
            self, config.make_name("HttpsSG"), vpc=vpc, allow_all_outbound=True
        )
        https_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443))

        cluster = ecs.Cluster(
            self,
            config.make_name("DeploymentCluster"),
            vpc=vpc,
            container_insights=True,
            cluster_name=config.ecs_cluster_name,
            enable_fargate_capacity_providers=True,
        )

        # See https://blog.cloudglance.dev/deep-dive-on-ecs-desired-count-and-circuit-breaker-rollback/index.html
        django_max_capacity = 5
        django_web_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            config.make_name("DjangoWebService"),
            cluster=cluster,
            security_groups=[http_sg, https_sg],
            desired_count=django_max_capacity,
            public_load_balancer=True,
            load_balancer_name=config.make_name("LoadBalancer"),
            service_name=config.ecs_django_service_name,
            certificate=self.domain_stack.certificate,
            redirect_http=True,
            protocol=elb.ApplicationProtocol.HTTPS,
            task_definition=self._get_web_task_definition(ecr_repo, config),
            enable_execute_command=True,
            circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
        )

        # Setup AutoScaling policy
        scaling = django_web_service.service.auto_scale_task_count(
            max_capacity=django_max_capacity,
            min_capacity=2,
        )
        scaling.scale_on_cpu_utilization(
            config.make_name("CpuScaling"),
            target_utilization_percent=50,
            scale_in_cooldown=cdk.Duration.seconds(120),
            scale_out_cooldown=cdk.Duration.seconds(120),
        )

        # print out fargateService load balancer url
        cdk.CfnOutput(
            self,
            config.make_name("DjangoWebDNS"),
            value=django_web_service.load_balancer.load_balancer_dns_name,
        )

        # See https://blog.cloudglance.dev/deep-dive-on-ecs-desired-count-and-circuit-breaker-rollback/index.html
        celery_max_capacity = 5
        celery_worker_service = ecs.FargateService(
            self,
            config.make_name("CeleryService"),
            cluster=cluster,
            desired_count=celery_max_capacity,
            service_name=config.ecs_celery_service_name,
            task_definition=self._get_celery_task_definition(
                ecr_repo, config, is_beat=False
            ),
            enable_execute_command=True,
            circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
            capacity_provider_strategies=[
                ecs.CapacityProviderStrategy(
                    capacity_provider="FARGATE",
                    base=1,  # 1 worker always on standard Fargate for guaranteed capacity
                    weight=0,
                ),
                ecs.CapacityProviderStrategy(
                    capacity_provider="FARGATE_SPOT",
                    weight=1,  # all additional workers on Spot (~70% savings)
                ),
            ],
        )

        celery_scaling = celery_worker_service.auto_scale_task_count(
            max_capacity=celery_max_capacity,
            min_capacity=2,
        )
        celery_scaling.scale_on_cpu_utilization(
            config.make_name("CeleryCpuScaling"),
            target_utilization_percent=50,
            scale_in_cooldown=cdk.Duration.seconds(120),
            scale_out_cooldown=cdk.Duration.seconds(120),
        )

        ecs.FargateService(
            self,
            config.make_name("CeleryBeatService"),
            cluster=cluster,
            desired_count=1,
            service_name=config.ecs_celery_beat_service_name,
            task_definition=self._get_celery_task_definition(
                ecr_repo, config, is_beat=True
            ),
            enable_execute_command=True,
            circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
            # we only ever want 1 beat service running
            max_healthy_percent=100,
            min_healthy_percent=0,
        )

        # Create migration task for one-off runs before deployments
        self.migration_task_definition = self._get_migration_task_definition(
            ecr_repo, config
        )

        # Get private subnets for migration task
        private_subnets = vpc.select_subnets(
            subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
        ).subnet_ids

        cdk.CfnOutput(
            self,
            config.make_name("MigrationTaskArn"),
            value=self.migration_task_definition.task_definition_arn,
            description="ARN of the migration task definition for one-off runs",
        )

        cdk.CfnOutput(
            self,
            config.make_name("ClusterName"),
            value=cluster.cluster_name,
        )

        cdk.CfnOutput(
            self,
            config.make_name("PrivateSubnets"),
            value=",".join(private_subnets),
        )

        cdk.CfnOutput(
            self,
            config.make_name("ServiceSecurityGroup"),
            value=django_web_service.service.connections.security_groups[
                0
            ].security_group_id,
        )

        return django_web_service

    def _get_web_task_definition(self, ecr_repo, config: OCSConfig):
        log_group = self._get_log_group(config.make_name(config.LOG_GROUP_DJANGO))
        log_driver = ecs.AwsLogDriver(
            stream_prefix=config.make_name(), log_group=log_group
        )

        image = ecs.ContainerImage.from_ecr_repository(ecr_repo, tag="latest")
        django_task = ecs.FargateTaskDefinition(
            self,
            id=config.make_name("Django"),
            cpu=1024,  # 1 vCPU
            memory_limit_mib=2048,  # 2 GB memory
            execution_role=self.execution_role,
            task_role=self.task_role,
            family=config.make_name("Django"),
        )
        first_allowed_host = config.allowed_hosts.split(",")[0].strip()
        django_task.add_container(
            id="web",
            image=image,
            container_name="web",
            essential=True,
            port_mappings=[ecs.PortMapping(container_port=self.config.CONTAINER_PORT)],
            environment=self.django_env,
            secrets=self.secrets_dict,
            logging=log_driver,
            health_check=ecs.HealthCheck(
                command=[
                    "CMD-SHELL",
                    f"curl -H 'Host: {first_allowed_host}' -fISs http://localhost:8000/ -o /dev/null || exit 1",
                ],
                interval=cdk.Duration.seconds(30),
                timeout=cdk.Duration.seconds(5),
                retries=4,
            ),
        )

        return django_task

    def _get_migration_task_definition(self, ecr_repo, config: OCSConfig):
        """Create a dedicated task definition for running Django migrations.

        This task should be run as a one-off ECS task before deploying
        new versions of the web service.
        """
        log_group = self._get_log_group(
            config.make_name(config.LOG_GROUP_DJANGO_MIGRATIONS)
        )
        log_driver = ecs.AwsLogDriver(
            stream_prefix=config.make_name("migrate"), log_group=log_group
        )

        image = ecs.ContainerImage.from_ecr_repository(ecr_repo, tag="latest")
        migration_task = ecs.FargateTaskDefinition(
            self,
            id=config.make_name("Migration"),
            cpu=512,
            memory_limit_mib=1024,
            execution_role=self.execution_role,
            task_role=self.task_role,
            family=config.make_name("Migration"),
        )

        migration_task.add_container(
            id="migrate",
            image=image,
            container_name="migrate",
            command=["python", "manage.py", "migrate"],
            essential=True,
            environment=self.django_env,
            secrets=self.secrets_dict,
            logging=log_driver,
        )

        return migration_task

    def _get_log_group(self, name):
        return logs.LogGroup(
            self,
            name,
            log_group_name=name,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            retention=logs.RetentionDays.TWO_YEARS,
        )

    def _get_celery_task_definition(self, ecr_repo, config: OCSConfig, is_beat):
        if is_beat:
            log_group_name = config.LOG_GROUP_BEAT
            name = "CeleryBeatTask"
            pidfile = "/tmp/celerybeat.pid"
            command = (
                f"celery -A gpt_playground beat -l INFO --pidfile {pidfile}".split(" ")
            )
            container_name = "celery-beat"
            health_check = ecs.HealthCheck(
                command=[
                    "CMD-SHELL",
                    f"test -f {pidfile}",
                ],
                interval=cdk.Duration.seconds(30),
                timeout=cdk.Duration.seconds(5),
                retries=4,
            )
            cpu = 256
            memory = 512
        else:
            log_group_name = config.LOG_GROUP_CELERY
            name = "CeleryWorkerTask"
            command = "celery -A gpt_playground worker -l INFO --pool=threads --concurrency 10".split(
                " "
            )
            container_name = "celery-worker"
            cpu = 512  # 0.5 vCPU
            memory = 2048
            health_check = None  # disable for now
            # health_check = ecs.HealthCheck(
            #     command=[
            #         "CMD-SHELL",
            #         "celery -A gpt_playground inspect ping --destination celery@$HOSTNAME",
            #     ],
            #     interval=cdk.Duration.seconds(30),
            #     timeout=cdk.Duration.seconds(5),
            #     retries=4,
            # )

        log_group = self._get_log_group(config.make_name(log_group_name))
        log_driver = ecs.AwsLogDriver(
            stream_prefix=config.make_name(), log_group=log_group
        )

        image = ecs.ContainerImage.from_ecr_repository(ecr_repo, tag="latest")

        celery_task = ecs.FargateTaskDefinition(
            self,
            id=config.make_name(name),
            cpu=cpu,
            memory_limit_mib=memory,
            execution_role=self.execution_role,
            task_role=self.task_role,
            family=config.make_name(name),
        )

        celery_task.add_container(
            id=container_name,
            image=image,
            container_name=container_name,
            essential=True,
            environment=self.celery_env,
            secrets=self.secrets_dict,
            logging=log_driver,
            command=command,
            health_check=health_check,
        )

        return celery_task

    @cached_property
    def secrets_dict(self):
        django_secret_key = secretsmanager.Secret(
            self,
            self.config.django_secret_key_secrets_name,
            secret_name=self.config.django_secret_key_secrets_name,
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=50,
            ),
        )
        secrets = {
            "DJANGO_DATABASE_USER": ecs.Secret.from_secrets_manager(
                self.rds_stack.db_instance.secret, field="username"
            ),
            "DJANGO_DATABASE_PASSWORD": ecs.Secret.from_secrets_manager(
                self.rds_stack.db_instance.secret, field="password"
            ),
            "REDIS_URL": ecs.Secret.from_secrets_manager(
                self.redis_stack.redis_url_secret
            ),
            "SECRET_KEY": ecs.Secret.from_secrets_manager(django_secret_key),
            # Use IAM roles for access to these
            # "AWS_SECRET_ACCESS_KEY":
            # "AWS_SES_ACCESS_KEY":
            # "AWS_SES_REGION":
            # "AWS_SES_SECRET_KEY":
        }
        for secret in self.config.get_existing_secrets_list():
            secrets[secret.env_var] = ecs.Secret.from_secrets_manager(
                secretsmanager.Secret.from_secret_name_v2(
                    self, secret.name, secret.name
                )
            )
        return secrets

    @cached_property
    def django_env(self):
        return self.config.get_django_env(
            rds_host=self.rds_stack.rds_proxy.endpoint,
            rds_port=self.rds_stack.db_instance.db_instance_endpoint_port,
        )

    @cached_property
    def celery_env(self):
        return self.config.get_celery_env(
            rds_host=self.rds_stack.rds_proxy.endpoint,
            rds_port=self.rds_stack.db_instance.db_instance_endpoint_port,
        )

    @cached_property
    def execution_role(self):
        """Task execution role with access to read from ECS"""
        execution_role = iam.Role(
            self,
            "ecsTaskExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            role_name=self.config.ecs_task_execution_role,
        )
        # Add permissions to the Task Role
        execution_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AmazonECSTaskExecutionRolePolicy"
            )
        )
        # Add permissions to the Task Role to allow it to pull images from ECR
        execution_role.add_to_policy(
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
        return execution_role

    @cached_property
    def task_role(self):
        """Task role used by the containers."""
        task_role = iam.Role(
            self,
            "ecsTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            role_name=self.config.ecs_task_role_name,
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3:PutObject",
                    "s3:GetObjectAcl",
                    "s3:GetObject",
                    "s3:ListBucket",
                    "s3:DeleteObject",
                    "s3:PutObjectAcl",
                    "s3:GetBucketAcl",
                ],
                effect=iam.Effect.ALLOW,
                resources=[
                    f"arn:aws:s3:::{self.config.s3_private_bucket_name}",
                    f"arn:aws:s3:::{self.config.s3_private_bucket_name}/*",
                    f"arn:aws:s3:::{self.config.s3_public_bucket_name}",
                    f"arn:aws:s3:::{self.config.s3_public_bucket_name}/*",
                    f"arn:aws:s3:::{self.config.s3_whatsapp_audio_bucket}",
                    f"arn:aws:s3:::{self.config.s3_whatsapp_audio_bucket}/*",
                ],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ses:SendEmail",
                    "ses:SendRawEmail",
                    "ses:SendBulkEmail",
                ],
                effect=iam.Effect.ALLOW,
                resources=["*"],
            )
        )
        return task_role
