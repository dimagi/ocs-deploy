import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from constructs import Construct

from ocs_deploy.config import OCSConfig


class Ec2TmpStack(cdk.Stack):
    def __init__(self, scope: Construct, vpc, config: OCSConfig) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.EC2_TMP_STACK), env=config.env()
        )

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list',
            "curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/postgresql.gpg",
            "apt-get update -y",
            "apt-get install -y postgresql-client-16 unzip",
            'curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"',
            "unzip awscliv2.zip",
            "./aws/install",
            "rm -rf ./aws awscliv2.zip",
        )

        # see https://documentation.ubuntu.com/aws/en/latest/aws-how-to/instances/find-ubuntu-images/
        machine_image = ec2.MachineImage.from_ssm_parameter(
            "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id",
            os=ec2.OperatingSystemType.LINUX,
            user_data=user_data,
        )

        name = config.make_name("TmpInstance")
        instance = ec2.Instance(
            self,
            name,
            vpc=vpc,
            instance_type=ec2.InstanceType("t2.micro"),
            machine_image=machine_image,
            ssm_session_permissions=True,
        )

        cdk.CfnOutput(
            self,
            config.make_name(f"{name}InstanceId"),
            value=instance.instance_id,
            export_name="EC2TmpInstanceID",
        )
