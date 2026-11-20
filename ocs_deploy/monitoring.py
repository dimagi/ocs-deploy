import aws_cdk as cdk
from aws_cdk import (
    aws_chatbot as chatbot,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_elasticloadbalancingv2 as elb,
    aws_logs as logs,
    aws_sns as sns,
)
from constructs import Construct

from ocs_deploy.config import OCSConfig


class MonitoringStack(cdk.Stack):
    """CloudWatch alarms for the app's ALB, ECS services, RDS, Redis and WAF,
    published to Slack via an SNS topic and AWS Chatbot, plus metrics and a
    dashboard built from the app's rate-limit log lines."""

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
        self._create_rate_limit_monitoring(config)

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
            target_group.metrics.http_code_target(
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
            target_group.metrics.target_response_time(
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
            target_group.metrics.unhealthy_host_count(period=cdk.Duration.minutes(1)),
            threshold=0,
            evaluation_periods=3,
            comparison=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="One or more Django targets are failing ALB health checks.",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self._alarm(
            "AlbRejectedConnectionsAlarm",
            load_balancer.metrics.rejected_connection_count(
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
        # Don't rely on RDS autoscaling to grow the volume at 10% free: in
        # practice it has left free space below 10% for weeks and only grown
        # the volume once it was nearly full. The 15% alarm is an early
        # warning; the 5% alarm means the disk could fill before autoscaling
        # acts, so add storage manually. Percentages rather than fixed byte
        # counts: on a 20 GiB volume, "below 10 GiB free" is just "over half
        # full".
        for alarm_id, percent in (
            ("RdsFreeStorageAlarm", 15),
            ("RdsFreeStorageCriticalAlarm", 5),
        ):
            self._alarm(
                alarm_id,
                db.metric_free_storage_space(period=cdk.Duration.minutes(5)),
                threshold=int(percent / 100 * config.rds_allocated_storage * 1024**3),
                evaluation_periods=3,
                comparison=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
                alarm_description=(
                    f"RDS free storage is below {percent}% of the "
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

    def _create_rate_limit_monitoring(self, config: OCSConfig) -> None:
        """Metrics, alarms, a dashboard and a saved query from the rate limiter's
        JSON log lines (`apps/utils/rate_limit.py` in open-chat-studio).

        Every caller of the limiter runs in the web service, so only the Django
        log group is watched. The group is looked up by name rather than passed
        from the Fargate stack so it doesn't add a cross-stack export.

        `would_block` and `blocked` are sampled by the app (the first crossing
        per identity per window, then every 100th request), so these metrics
        count log lines, not requests: read them as "an identity went over its
        limit", not as request volume. Per-identity detail (`key_hash`,
        `team_id`) stays in Logs Insights; making it a dimension would create a
        billed custom metric per identity.
        """
        log_group = logs.LogGroup.from_log_group_name(
            self,
            config.make_name("RateLimitDjangoLogGroup"),
            config.make_name(config.LOG_GROUP_DJANGO),
        )
        namespace = f"{config.make_name()}/RateLimit"

        def pattern(event):
            return logs.FilterPattern.all(
                logs.FilterPattern.string_value("$.logger", "=", "ocs.rate_limit"),
                logs.FilterPattern.string_value(
                    "$.message", "=", f"rate_limit.{event}"
                ),
            )

        def metric_filter(id_, event, metric_name, dimensions=None):
            logs.MetricFilter(
                self,
                config.make_name(id_),
                log_group=log_group,
                filter_pattern=pattern(event),
                metric_namespace=namespace,
                metric_name=metric_name,
                metric_value="1",
                dimensions=dimensions,
            )

        # Broken down by scope, for the dashboard. SEARCH picks up every scope
        # the app emits, so a new scope needs no change here.
        by_scope = {
            "near_limit": (
                "RateLimitNearLimit",
                {"scope": "$.scope", "percent": "$.percent"},
            ),
            "would_block": ("RateLimitWouldBlock", {"scope": "$.scope"}),
            "blocked": ("RateLimitBlocked", {"scope": "$.scope"}),
            "backend_error": ("RateLimitBackendError", {"scope": "$.scope"}),
        }
        for event, (metric_name, dimensions) in by_scope.items():
            metric_filter(f"{metric_name}Filter", event, metric_name, dimensions)

        # An alarm needs a single metric, and a metric with dimensions can't be
        # summed across them, so the alarmed events also get a total.
        alarmed = {
            "blocked": (
                "RateLimitBlockedTotal",
                "Requests are being refused with 429 by the app's rate limiter. "
                "Check the rate-limit dashboard for the scope and Logs Insights "
                "for the identity.",
            ),
            "backend_error": (
                "RateLimitBackendErrorTotal",
                "The rate limiter can't reach Redis. Scopes that fail open are "
                "serving requests without a limit; scopes that fail closed are "
                "refusing them.",
            ),
        }
        for event, (metric_name, description) in alarmed.items():
            metric_filter(f"{metric_name}Filter", event, metric_name)
            self._alarm(
                f"{metric_name}Alarm",
                cloudwatch.Metric(
                    namespace=namespace,
                    metric_name=metric_name,
                    statistic="Sum",
                    period=cdk.Duration.minutes(5),
                ),
                # Starting point only: once enforcement is on, raise the
                # `blocked` threshold if routine refusals make it noisy.
                threshold=0,
                evaluation_periods=1,
                comparison=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                alarm_description=description,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )

        query_string = logs.QueryString(
            fields=[
                "scope",
                "identity_type",
                "key_hash",
                "team_id",
                "count",
                "limit",
                "percent",
            ],
            filter_statements=[
                'logger = "ocs.rate_limit" and message in '
                '["rate_limit.near_limit", "rate_limit.would_block", "rate_limit.blocked"]'
            ],
            stats="max(count) as peak, max(limit) as lim by scope, identity_type, key_hash, team_id",
            sort="peak desc",
        )
        logs.QueryDefinition(
            self,
            config.make_name("RateLimitBusiestIdentitiesQuery"),
            query_definition_name=f"{config.make_name()}/rate-limit-busiest-identities",
            query_string=query_string,
            log_groups=[log_group],
        )

        def search_graph(title, metric_name, dimensions):
            dims = ",".join(dimensions)
            return cloudwatch.GraphWidget(
                title=title,
                width=12,
                left=[
                    cloudwatch.MathExpression(
                        expression=(
                            f"SEARCH('{{{namespace},{dims}}} "
                            f"MetricName=\"{metric_name}\"', 'Sum', 300)"
                        ),
                        label="",
                        period=cdk.Duration.minutes(5),
                        using_metrics={},
                    )
                ],
            )

        dashboard = cloudwatch.Dashboard(
            self,
            config.make_name("RateLimitDashboard"),
            dashboard_name=config.make_name("rate-limits"),
        )
        dashboard.add_widgets(
            cloudwatch.TextWidget(
                markdown=(
                    "### Rate limits\n"
                    "Counts are log lines, not requests: `would_block` and "
                    "`blocked` log the first crossing per identity per window, "
                    "then every 100th request. `near_limit` logs each identity "
                    "once per window at 50% and 80% of its limit."
                ),
                width=24,
                height=2,
            )
        )
        dashboard.add_widgets(
            search_graph(
                "Near limit (by scope, percent)",
                "RateLimitNearLimit",
                ["scope", "percent"],
            ),
            search_graph("Would block (by scope)", "RateLimitWouldBlock", ["scope"]),
        )
        dashboard.add_widgets(
            search_graph("Blocked (by scope)", "RateLimitBlocked", ["scope"]),
            search_graph(
                "Backend errors (by scope)", "RateLimitBackendError", ["scope"]
            ),
        )
        dashboard.add_widgets(
            cloudwatch.LogQueryWidget(
                title="Busiest identities",
                log_group_names=[log_group.log_group_name],
                query_string=query_string.to_string(),
                width=24,
                height=8,
            )
        )
