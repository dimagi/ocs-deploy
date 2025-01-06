import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_logs as logs,
    aws_rds as rds,
)
from constructs import Construct

from ocs_deploy.config import OCSConfig


class RdsStack(cdk.Stack):
    def __init__(self, scope: Construct, vpc, config: OCSConfig) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.RDS_STACK), env=config.cdk_env()
        )

        self.db_instance = self.setup_rds_database(vpc, config)

    def setup_rds_database(self, vpc, config: OCSConfig):
        db_server_sg = ec2.SecurityGroup(
            self,
            config.make_name("RdsSG"),
            security_group_name=config.make_name("RdsSG"),
            vpc=vpc,
            allow_all_outbound=True,
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

        database_username = "ocs_db_user"

        # define postgresql database
        db_instance = rds.DatabaseInstance(
            self,
            config.make_name("PostgresRDS"),
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16,
            ),
            # db.t4g.small
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T4G, ec2.InstanceSize.SMALL
            ),
            allocated_storage=20,
            max_allocated_storage=100,
            storage_encrypted=True,
            credentials=rds.Credentials.from_generated_secret(
                database_username,
                secret_name=config.make_secret_name("rds-credentials"),
            ),
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
            database_name=config.rds_db_name,
            preferred_maintenance_window=config.maintenance_window,
            backup_retention=cdk.Duration.days(7),
            preferred_backup_window="03:00-06:00",
            cloudwatch_logs_exports=["postgresql", "upgrade"],
            cloudwatch_logs_retention=logs.RetentionDays.ONE_MONTH,
            parameters={
                "autovacuum": "on",
                "client_encoding": "UTF8",
            },
        )
        db_instance.grant_connect(rds_role, database_username)

        port = db_instance.db_instance_endpoint_port

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
