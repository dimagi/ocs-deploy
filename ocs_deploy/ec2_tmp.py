import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from constructs import Construct

from ocs_deploy.config import OCSConfig


class Ec2TmpStack(cdk.Stack):
    def __init__(self, scope: Construct, vpc, config: OCSConfig) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.EC2_TMP_STACK), env=config.env()
        )

        name = config.make_name("TmpInstance")
        instance = ec2.Instance(
            self,
            name,
            vpc=vpc,
            instance_type=ec2.InstanceType("t2.micro"),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
            ssm_session_permissions=True,
        )

        cdk.CfnOutput(
            self,
            config.make_name(f"{name}InstanceId"),
            value=instance.instance_id,
            export_name="EC2TmpInstanceID",
        )
