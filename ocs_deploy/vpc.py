import aws_cdk as cdk
from aws_cdk import (
    NestedStack,
    aws_ec2 as ec2,
)
from constructs import Construct

from ocs_deploy.config import OCSConfig


class VpcStack(NestedStack):
    def __init__(self, scope: Construct, config: OCSConfig) -> None:
        super().__init__(scope, config.stack_name("VPC"))
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
        return vpc
