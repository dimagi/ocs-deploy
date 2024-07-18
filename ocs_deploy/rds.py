import json

import aws_cdk as cdk
from aws_cdk import (
    NestedStack,
    aws_rds as rds,
    aws_ec2 as ec2,
    aws_logs as logs,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

from ocs_deploy.config import OCSConfig


class RdsStack(NestedStack):
    def __init__(self, scope: Construct, vpc, config: OCSConfig) -> None:
        super().__init__(scope, config.stack_name("FargateVpcDeployment"))
        self.rds_database = self.setup_rds_database(vpc, config)

    def setup_rds_database(self, vpc, config: OCSConfig):
        db_server_sg = ec2.SecurityGroup(
            self, config.make_name("RdsSG"), vpc=vpc, allow_all_outbound=True
        )
        db_server_sg.add_ingress_rule(
            ec2.Peer.ipv4(vpc.vpc_cidr_block), ec2.Port.tcp(5432)
        )

        # Create a new IAM role that can be assumed by the RDS service
        rds_role = iam.Role(
            self,
            config.make_name("RDSRole"),
            assumed_by=iam.ServicePrincipal("rds.amazonaws.com"),
            role_name=config.make_name("RDSRole"),
        )

        rds_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "rds-db:connect",
                    "secretsmanager:GetSecretValue",
                    "sts:AssumeRole",
                ],
                resources=["*"],
            )
        )
        rds_role.apply_removal_policy(cdk.RemovalPolicy.DESTROY)

        rds_credentials = secretsmanager.Secret(
            self,
            config.make_name("RdsCredentials"),
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template=json.dumps({"username": config.rds_username}),
                generate_string_key="password",
                exclude_characters="/@",
            ),
        )

        # define postgresql database
        db_instance = rds.DatabaseInstance(
            self,
            config.make_name("PostgresRDS"),
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16,
            ),
            # db.t4g.small
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE3, ec2.InstanceSize.SMALL
            ),
            allocated_storage=20,
            max_allocated_storage=100,
            storage_encrypted=True,
            credentials={
                "username": rds_credentials.secret_value_from_json(
                    "username"
                ).to_string(),
                "password": rds_credentials.secret_value_from_json("password"),
            },
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
            ),
            auto_minor_version_upgrade=True,
            allow_major_version_upgrade=False,
            security_groups=[db_server_sg],
            multi_az=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            deletion_protection=True,
            publicly_accessible=False,
            database_name=config.rds_database_name,
            preferred_maintenance_window="Mon:00:00-Mon:03:00",
            backup_retention=cdk.Duration.days(7),
            preferred_backup_window="03:00-06:00",
            cloudwatch_logs_exports=["postgresql", "upgrade"],
            cloudwatch_logs_retention=logs.RetentionDays.ONE_MONTH,
            parameters={
                "autovacuum": "on",
                "client_encoding": "UTF8",
            },
        )
        db_instance.grant_connect(rds_role, config.rds_username)

        hostname = db_instance.instance_endpoint.hostname
        port = db_instance.db_instance_endpoint_port
        db_url = f"postgres://{config.rds_username}:{rds_credentials.secret_value_from_json('password').to_string()}@{hostname}:{port}/{config.rds_database_name}"

        secretsmanager.Secret(
            self,
            config.rds_url_secrets_name,
            secret_name=config.rds_url_secrets_name,
            secret_string_value=cdk.SecretValue.unsafe_plain_text(db_url),
        )

        cdk.CfnOutput(
            self,
            config.make_name("PostgresDatabaseInstanceHostname"),
            export_name=config.make_name("PostgresDatabaseInstanceHostname"),
            value=hostname,
            description="PostgreSQL database instance hostname.",
        )

        cdk.CfnOutput(
            self,
            config.make_name("PostgresDatabaseInstanceAddress"),
            export_name=config.make_name("PostgresDatabaseInstanceAddress"),
            value=db_instance.db_instance_endpoint_address,
            description="PostgreSQL database instance address.",
        )

        cdk.CfnOutput(
            self,
            config.make_name("PostgresDatabaseInstancePort"),
            export_name=config.make_name("PostgresDatabaseInstancePort"),
            value=port,
            description="PostgreSQL database instance port.",
        )

        return db_instance
