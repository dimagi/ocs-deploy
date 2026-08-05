import aws_cdk as cdk
import aws_cdk.assertions as assertions

from ocs_deploy.domains import DomainStack
from ocs_deploy.ecr import EcrStack
from ocs_deploy.fargate import FargateStack
from ocs_deploy.monitoring import MonitoringStack
from ocs_deploy.rds import RdsStack
from ocs_deploy.redis import RedisStack
from ocs_deploy.ses_inbound import SesInboundStack
from ocs_deploy.vpc import VpcStack
from ocs_deploy.waf import WAFStack


def _synth_monitoring(config):
    app = cdk.App()
    domain_stack = DomainStack(app, config)
    vpc_stack = VpcStack(app, config)
    ecr_stack = EcrStack(app, config)
    rds_stack = RdsStack(app, vpc_stack.vpc, config)
    redis_stack = RedisStack(app, vpc_stack.vpc, config)
    ses_inbound_stack = SesInboundStack(app, config)
    fargate_stack = FargateStack(
        app,
        vpc_stack.vpc,
        ecr_stack.repo,
        rds_stack,
        redis_stack,
        domain_stack,
        ses_inbound_stack,
        config,
    )
    waf_stack = WAFStack(app, config, fargate_stack.load_balancer_arn)
    monitoring_stack = MonitoringStack(
        app, fargate_stack, rds_stack, redis_stack, waf_stack, config
    )
    return assertions.Template.from_stack(monitoring_stack)


def test_alarm_topic_created(ocs_config):
    template = _synth_monitoring(ocs_config)
    template.resource_count_is("AWS::SNS::Topic", 1)


def test_no_slack_channel_without_config(ocs_config):
    """Without Slack env vars, alarms still wire up but Chatbot doesn't."""
    template = _synth_monitoring(ocs_config)
    template.resource_count_is("AWS::Chatbot::SlackChannelConfiguration", 0)
    outputs = template.to_json()["Outputs"]
    assert any("SlackChatbotSetupRequired" in key for key in outputs)


def test_slack_channel_created_with_config(ocs_config_factory):
    config = ocs_config_factory(
        SLACK_WORKSPACE_ID="T0000000000",
        SLACK_ALERTS_CHANNEL_ID="C0000000000",
    )
    template = _synth_monitoring(config)
    template.has_resource_properties(
        "AWS::Chatbot::SlackChannelConfiguration",
        {
            "SlackWorkspaceId": "T0000000000",
            "SlackChannelId": "C0000000000",
        },
    )


def test_alarms_notify_the_alarm_topic(ocs_config):
    template = _synth_monitoring(ocs_config)
    alarms = template.find_resources("AWS::CloudWatch::Alarm")
    assert len(alarms) > 0
    for props in alarms.values():
        actions = props["Properties"]["AlarmActions"]
        assert len(actions) == 1


def test_ecs_no_running_tasks_alarm_uses_container_insights(ocs_config):
    template = _synth_monitoring(ocs_config)
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        assertions.Match.object_like(
            {
                "Namespace": "ECS/ContainerInsights",
                "MetricName": "RunningTaskCount",
                "ComparisonOperator": "LessThanThreshold",
                "TreatMissingData": "breaching",
            }
        ),
    )


def test_redis_alarms_use_cache_cluster_id_dimension(ocs_config):
    template = _synth_monitoring(ocs_config)
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        assertions.Match.object_like(
            {
                "Namespace": "AWS/ElastiCache",
                "MetricName": "EngineCPUUtilization",
                "Dimensions": [
                    {
                        "Name": "CacheClusterId",
                        "Value": assertions.Match.any_value(),
                    }
                ],
            }
        ),
    )
