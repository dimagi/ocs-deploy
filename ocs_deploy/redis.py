import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_logs as logs,
    aws_elasticache as elasticache,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

from ocs_deploy.config import OCSConfig


class RedisStack(cdk.Stack):
    def __init__(self, scope: Construct, vpc, config: OCSConfig) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.REDIS_STACK), env=config.cdk_env()
        )

        self.setup_redis_database(vpc, config)

    def setup_redis_database(self, vpc, config: OCSConfig):
        redis_sec_group = ec2.SecurityGroup(
            self,
            config.make_name("RedisSG"),
            security_group_name=config.make_name("RedisSG"),
            vpc=vpc,
            allow_all_outbound=True,
        )

        redis_sec_group.add_ingress_rule(
            ec2.Peer.ipv4(vpc.vpc_cidr_block),
            ec2.Port.tcp(6379),
        )

        private_subnets_ids = [ps.subnet_id for ps in vpc.private_subnets]

        redis_subnet_group = elasticache.CfnSubnetGroup(
            scope=self,
            id=config.make_name("RedisSubnetGroup"),
            subnet_ids=private_subnets_ids,
            description=config.make_name("RedisSubnetGroup"),
        )

        engine_log_group = self.create_cloudwatch_log_group(
            config.make_name("RedisEngineLogs")
        )
        slow_log_group = self.create_cloudwatch_log_group(
            config.make_name("RedisSlowLogs")
        )

        self.redis_cluster = elasticache.CfnCacheCluster(
            scope=self,
            id=config.make_name("RedisCluster"),
            cluster_name=config.make_name("RedisCluster"),
            engine="redis",
            engine_version="7.1",
            cache_node_type="cache.t3.small",
            num_cache_nodes=1,
            cache_subnet_group_name=redis_subnet_group.ref,
            vpc_security_group_ids=[redis_sec_group.security_group_id],
            auto_minor_version_upgrade=True,
            preferred_maintenance_window=config.maintenance_window,
            log_delivery_configurations=[
                # TODO: create log groups
                elasticache.CfnCacheCluster.LogDeliveryConfigurationRequestProperty(
                    destination_details=elasticache.CfnCacheCluster.DestinationDetailsProperty(
                        cloud_watch_logs_details=elasticache.CfnCacheCluster.CloudWatchLogsDestinationDetailsProperty(
                            log_group=engine_log_group.log_group_name,
                        ),
                    ),
                    destination_type="cloudwatch-logs",
                    log_format="json",
                    log_type="engine-log",
                ),
                elasticache.CfnCacheCluster.LogDeliveryConfigurationRequestProperty(
                    destination_details=elasticache.CfnCacheCluster.DestinationDetailsProperty(
                        cloud_watch_logs_details=elasticache.CfnCacheCluster.CloudWatchLogsDestinationDetailsProperty(
                            log_group=slow_log_group.log_group_name,
                        ),
                    ),
                    destination_type="cloudwatch-logs",
                    log_format="json",
                    log_type="slow-log",
                ),
            ],
        )

        redis_url = f"redis://{self.redis_cluster.attr_redis_endpoint_address}:{self.redis_cluster.attr_redis_endpoint_port}"

        self.redis_url_secret = secretsmanager.Secret(
            self,
            config.redis_url_secrets_name,
            secret_name=config.redis_url_secrets_name,
            secret_string_value=cdk.SecretValue.unsafe_plain_text(redis_url),
        )

    def create_cloudwatch_log_group(self, name):
        return logs.LogGroup(
            self,
            name,
            log_group_name=name,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            retention=logs.RetentionDays.ONE_MONTH,
        )
