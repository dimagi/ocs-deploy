import aws_cdk as cdk
from aws_cdk import (
    aws_chatbot as chatbot,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_elasticloadbalancingv2 as elb,
    aws_sns as sns,
)
from constructs import Construct

from ocs_deploy.config import OCSConfig


class MonitoringStack(cdk.Stack):
    """CloudWatch alarms for the app's ALB, ECS services, RDS, Redis and WAF,
    published to Slack via an SNS topic and AWS Chatbot."""

    def __init__(
        self,
        scope: Construct,
        fargate_stack,
        rds_stack,
        redis_stack,
        waf_stack,
        config: OCSConfig,
    ) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.MONITORING_STACK), env=config.cdk_env()
        )
        self.config = config
        self.alarm_topic = self._create_alarm_topic(config)
        self._create_slack_channel(config)
        self._create_alb_alarms(fargate_stack, config)
        self._create_ecs_alarms(fargate_stack, config)
        self._create_rds_alarms(rds_stack, config)
        self._create_redis_alarms(redis_stack, config)
        self._create_waf_alarms(waf_stack, config)

    def _create_alarm_topic(self, config: OCSConfig) -> sns.Topic:
        topic = sns.Topic(
            self,
            config.make_name("AlarmTopic"),
            topic_name=config.make_name("alarms"),
            display_name="OCS CloudWatch Alarms",
        )
        cdk.CfnOutput(
            self,
            config.make_name("AlarmTopicArn"),
            value=topic.topic_arn,
        )
        return topic

    def _create_slack_channel(self, config: OCSConfig) -> None:
        if not (config.slack_workspace_id and config.slack_alerts_channel_id):
            cdk.CfnOutput(
                self,
                config.make_name("SlackChatbotSetupRequired"),
                value=(
                    "Alarms publish to the topic above but AWS Chatbot isn't "
                    "configured yet. Authorize your Slack workspace in the AWS "
                    "Chatbot console, then set SLACK_WORKSPACE_ID and "
                    "SLACK_ALERTS_CHANNEL_ID and redeploy."
                ),
            )
            return

        chatbot.SlackChannelConfiguration(
            self,
            config.make_name("SlackChannel"),
            slack_channel_configuration_name=config.make_name("alarms"),
            slack_channel_id=config.slack_alerts_channel_id,
            slack_workspace_id=config.slack_workspace_id,
            notification_topics=[self.alarm_topic],
            logging_level=chatbot.LoggingLevel.ERROR,
        )

    def _alarm(self, id_, metric, threshold, evaluation_periods, comparison, **kwargs):
        alarm = cloudwatch.Alarm(
            self,
            self.config.make_name(id_),
            metric=metric,
            threshold=threshold,
            evaluation_periods=evaluation_periods,
            comparison_operator=comparison,
            **kwargs,
        )
        alarm.add_alarm_action(cw_actions.SnsAction(self.alarm_topic))
        alarm.add_ok_action(cw_actions.SnsAction(self.alarm_topic))
        return alarm

    def _create_alb_alarms(self, fargate_stack, config: OCSConfig) -> None:
        web_service = fargate_stack.fargate_service
        load_balancer = web_service.load_balancer
        target_group = web_service.target_group

        self._alarm(
            "AlbTarget5xxAlarm",
            target_group.metric_http_code_target(
                elb.HttpCodeTarget.TARGET_5XX_COUNT, period=cdk.Duration.minutes(5)
            ),
            threshold=10,
            evaluation_periods=3,
            comparison=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="Django is returning a sustained rate of 5xx responses.",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self._alarm(
            "AlbResponseTimeAlarm",
            target_group.metric_target_response_time(
                statistic="p99", period=cdk.Duration.minutes(5)
            ),
            threshold=5,
            evaluation_periods=3,
            comparison=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="p99 target response time is above 5s.",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self._alarm(
            "AlbUnhealthyHostsAlarm",
            target_group.metric_unhealthy_host_count(period=cdk.Duration.minutes(1)),
            threshold=0,
            evaluation_periods=3,
            comparison=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="One or more Django targets are failing ALB health checks.",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self._alarm(
            "AlbRejectedConnectionsAlarm",
            load_balancer.metric_rejected_connection_count(
                period=cdk.Duration.minutes(5)
            ),
            threshold=0,
            evaluation_periods=1,
            comparison=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="The ALB is rejecting connections (out of capacity).",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

    def _create_ecs_alarms(self, fargate_stack, config: OCSConfig) -> None:
        services = {
            "DjangoWeb": fargate_stack.fargate_service.service,
            "CeleryBeat": fargate_stack.celery_beat_service,
            **fargate_stack.celery_worker_services,
        }
        for name, service in services.items():
            self._alarm(
                f"{name}CpuAlarm",
                service.metric_cpu_utilization(period=cdk.Duration.minutes(5)),
                threshold=85,
                evaluation_periods=3,
                comparison=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                alarm_description=f"{name} CPU utilization is sustained above 85%.",
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            self._alarm(
                f"{name}MemoryAlarm",
                service.metric_memory_utilization(period=cdk.Duration.minutes(5)),
                threshold=85,
                evaluation_periods=3,
                comparison=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                alarm_description=f"{name} memory utilization is sustained above 85%.",
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            # Container Insights running-task count: catches a service stuck at
            # zero healthy tasks, which CPU/memory alone won't show.
            running_task_count = cloudwatch.Metric(
                namespace="ECS/ContainerInsights",
                metric_name="RunningTaskCount",
                dimensions_map={
                    "ClusterName": fargate_stack.cluster.cluster_name,
                    "ServiceName": service.service_name,
                },
                statistic="Minimum",
                period=cdk.Duration.minutes(1),
            )
            self._alarm(
                f"{name}NoRunningTasksAlarm",
                running_task_count,
                threshold=1,
                evaluation_periods=3,
                comparison=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
                alarm_description=f"{name} has no running tasks.",
                treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            )

    def _create_rds_alarms(self, rds_stack, config: OCSConfig) -> None:
        db = rds_stack.db_instance
        self._alarm(
            "RdsCpuAlarm",
            db.metric_cpu_utilization(period=cdk.Duration.minutes(5)),
            threshold=85,
            evaluation_periods=3,
            comparison=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="RDS CPU utilization is sustained above 85%.",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        # RDS autoscaling grows the volume once free space drops below 10% of
        # the allocated size, so alarm slightly above that: it warns while
        # autoscaling still has room, and stays useful once the volume reaches
        # the cap and autoscaling can no longer help. A fixed byte threshold
        # can't do this — on a 20 GiB volume, "below 10 GiB free" is just
        # "over half full".
        free_storage_threshold = int(0.15 * config.rds_allocated_storage * 1024**3)
        self._alarm(
            "RdsFreeStorageAlarm",
            db.metric_free_storage_space(period=cdk.Duration.minutes(5)),
            threshold=free_storage_threshold,
            evaluation_periods=3,
            comparison=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            alarm_description=(
                f"RDS free storage is below 15% of the "
                f"{config.rds_allocated_storage} GiB allocated volume."
            ),
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self._alarm(
            "RdsFreeableMemoryAlarm",
            db.metric_freeable_memory(period=cdk.Duration.minutes(5)),
            threshold=256 * 1024**2,  # 256 MiB
            evaluation_periods=3,
            comparison=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            alarm_description="RDS freeable memory is below 256 MiB.",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self._alarm(
            "RdsConnectionsAlarm",
            db.metric_database_connections(period=cdk.Duration.minutes(5)),
            threshold=200,
            evaluation_periods=3,
            comparison=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="RDS connection count is above 200.",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

    def _create_redis_alarms(self, redis_stack, config: OCSConfig) -> None:
        dimensions = {"CacheClusterId": redis_stack.redis_cluster.ref}

        def redis_metric(metric_name, statistic="Average"):
            return cloudwatch.Metric(
                namespace="AWS/ElastiCache",
                metric_name=metric_name,
                dimensions_map=dimensions,
                statistic=statistic,
                period=cdk.Duration.minutes(5),
            )

        self._alarm(
            "RedisCpuAlarm",
            redis_metric("EngineCPUUtilization"),
            threshold=85,
            evaluation_periods=3,
            comparison=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="Redis engine CPU utilization is sustained above 85%.",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self._alarm(
            "RedisMemoryAlarm",
            redis_metric("DatabaseMemoryUsagePercentage"),
            threshold=85,
            evaluation_periods=3,
            comparison=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="Redis memory usage is sustained above 85%.",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self._alarm(
            "RedisEvictionsAlarm",
            redis_metric("Evictions", statistic="Sum"),
            threshold=0,
            evaluation_periods=3,
            comparison=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="Redis is evicting keys due to memory pressure.",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

    def _create_waf_alarms(self, waf_stack, config: OCSConfig) -> None:
        dimensions = {
            "WebACL": config.make_name("DjangoWAFMetrics"),
            "Region": config.region,
            "Rule": "ALL",
        }
        blocked_requests = cloudwatch.Metric(
            namespace="AWS/WAFV2",
            metric_name="BlockedRequests",
            dimensions_map=dimensions,
            statistic="Sum",
            period=cdk.Duration.minutes(5),
        )
        # Starting point only — tune the threshold to this app's normal traffic
        # once there's a baseline.
        self._alarm(
            "WafBlockedRequestsAlarm",
            blocked_requests,
            threshold=1000,
            evaluation_periods=1,
            comparison=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="WAF is blocking an unusually high rate of requests.",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
