import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from constructs import Construct

from ocs_deploy.config import OCSConfig


class GithubOidcStack(cdk.Stack):
    """Create IAM Role for Github Actions"""

    def __init__(self, scope: Construct, config: OCSConfig) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.GITHUB_STACK), env=config.cdk_env()
        )

        self.setup_github_actions_role(config)

    def setup_github_actions_role(self, config: OCSConfig):
        arn = f"arn:aws:iam::{config.account}:oidc-provider/token.actions.githubusercontent.com"

        # Create provider, so github can connect to AWS
        iam.OpenIdConnectProvider(
            self,
            "token.actions.githubusercontent.com",
            url="https://token.actions.githubusercontent.com",
            client_ids=["sts.amazonaws.com"],
            thumbprints=["6938fd4d98bab03faadb97b34396831e3780aea1"],
        )

        principal = iam.WebIdentityPrincipal(
            arn,
            {
                "ForAllValues:StringEquals": {
                    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    "token.actions.githubusercontent.com:iss": "https://token.actions.githubusercontent.com",
                },
                "StringLike": {
                    "token.actions.githubusercontent.com:sub": f"repo:{config.github_repo}:*"
                },
            },
        )
        role = iam.Role(
            self,
            config.make_name("GithubActions"),
            assumed_by=principal,
            description="Role to access non-prod resources",
            role_name="github_deploy",
        )

        role.add_to_policy(
            iam.PolicyStatement(
                sid="PushToECR",
                actions=[
                    "ecr:BatchGetImage",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:CompleteLayerUpload",
                    "ecr:InitiateLayerUpload",
                    "ecr:PutImage",
                    "ecr:UploadLayerPart",
                ],
                effect=iam.Effect.ALLOW,
                resources=[
                    f"arn:aws:ecr:{config.region}:{config.account}:repository/{config.ecr_repo_name}"
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="GetECRToken",
                actions=[
                    "ecr:GetAuthorizationToken",
                ],
                effect=iam.Effect.ALLOW,
                resources=["*"],
            )
        )

        # See https://github.com/aws-actions/amazon-ecs-deploy-task-definition?tab=readme-ov-file#permissions
        role.add_to_policy(
            iam.PolicyStatement(
                sid="RegisterTaskDefinition",
                actions=[
                    "ecs:RegisterTaskDefinition",
                    "ecs:DescribeTaskDefinition",
                ],
                effect=iam.Effect.ALLOW,
                resources=["*"],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="PassRolesInTaskDefinition",
                actions=["iam:PassRole"],
                resources=[
                    f"arn:aws:iam::{config.account}:role/{config.ecs_task_role_name}",
                    f"arn:aws:iam::{config.account}:role/{config.ecs_task_execution_role}",
                ],
                effect=iam.Effect.ALLOW,
            )
        )
        service_prefix = f"arn:aws:ecs:{config.region}:{config.account}:service/{config.ecs_cluster_name}"
        role.add_to_policy(
            iam.PolicyStatement(
                sid="DeployService",
                actions=[
                    "ecs:UpdateService",
                    "ecs:DescribeServices",
                ],
                resources=[
                    f"{service_prefix}/{config.ecs_django_service_name}",
                    f"{service_prefix}/{config.ecs_celery_service_name}",
                    f"{service_prefix}/{config.ecs_celery_beat_service_name}",
                ],
                effect=iam.Effect.ALLOW,
            )
        )

        # Permissions for running migration task
        role.add_to_policy(
            iam.PolicyStatement(
                sid="RunMigrationTask",
                actions=[
                    "ecs:RunTask",
                ],
                resources=[
                    f"arn:aws:ecs:{config.region}:{config.account}:task-definition/{config.make_name('Migration')}:*"
                ],
                effect=iam.Effect.ALLOW,
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="DescribeTasks",
                actions=[
                    "ecs:DescribeTasks",
                ],
                resources=[
                    f"arn:aws:ecs:{config.region}:{config.account}:task/{config.ecs_cluster_name}/*"
                ],
                effect=iam.Effect.ALLOW,
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="GetStackOutputs",
                actions=[
                    "cloudformation:DescribeStacks",
                ],
                resources=[
                    f"arn:aws:cloudformation:{config.region}:{config.account}:stack/{config.stack_name(OCSConfig.DJANGO_STACK)}/*"
                ],
                effect=iam.Effect.ALLOW,
            )
        )
