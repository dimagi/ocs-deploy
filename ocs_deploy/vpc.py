import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2, aws_iam as iam, aws_logs as logs
from constructs import Construct

from ocs_deploy.config import OCSConfig


class VpcStack(cdk.Stack):
    def __init__(self, scope: Construct, config: OCSConfig) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.VPC_STACK), env=config.env()
        )
        self.vpc = self.setup_vpc(config)

    def setup_vpc(self, config: OCSConfig):
        """Set up a VPC in which the container will run"""
        vpc = ec2.Vpc(
            self,
            config.make_name("VPC"),
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            max_azs=3,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    subnet_type=ec2.SubnetType.PUBLIC,
                    name=config.make_name("Public"),
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    cidr_mask=24,
                    name=config.make_name("Private"),
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                ),
                ec2.SubnetConfiguration(
                    cidr_mask=24,
                    name=config.make_name("Isolated"),
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                ),
            ],
        )

        vpc.apply_removal_policy(cdk.RemovalPolicy.DESTROY)

        vpc.add_gateway_endpoint("S3", service=ec2.GatewayVpcEndpointAwsService.S3)

        # Needed for ECS tasks (managed in Fargate) to pull images
        vpc.add_interface_endpoint(
            "EcsEndpoint", service=ec2.InterfaceVpcEndpointAwsService.ECS
        )
        # Needed for fargate to pull initial image from ECR
        vpc.add_interface_endpoint(
            "EcrEndpoint", service=ec2.InterfaceVpcEndpointAwsService.ECR
        )
        # TODO: Unclear if we need this
        vpc.add_interface_endpoint(
            "EcrDockerEndpoint", service=ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER
        )

        self._setup_flow_logs(config, vpc)

        return vpc

    def _setup_flow_logs(self, config, vpc):
        # VPC Flow Logs
        vpc_flow_log_role = iam.Role(
            self,
            config.make_name("RoleVpcFlowLogs"),
            assumed_by=iam.ServicePrincipal("vpc-flow-logs.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchFullAccess"),
            ],
        )
        vpc_flow_log_group = logs.LogGroup(
            self,
            config.make_name("VpcFlowLogGroup"),
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        # Create the VPC Flow Log Stream
        logs.LogStream(
            self,
            config.make_name("VpcFlowLogStream"),
            log_group=vpc_flow_log_group,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        ec2.FlowLog(
            self,
            config.make_name("VpcFlowLog"),
            resource_type=ec2.FlowLogResourceType.from_vpc(vpc),
            destination=ec2.FlowLogDestination.to_cloud_watch_logs(
                vpc_flow_log_group, vpc_flow_log_role
            ),
            traffic_type=ec2.FlowLogTrafficType.ALL,
        )
