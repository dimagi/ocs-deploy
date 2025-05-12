from aws_cdk import Stack, CfnOutput, aws_detective as detective, aws_cloudtrail as cloudtrail, aws_s3 as s3, aws_ec2 as ec2
from constructs import Construct
from ocs_deploy.config import OCSConfig

class DetectiveStack(Stack):
    def __init__(self, scope: Construct, config: OCSConfig, **kwargs) -> None:
        super().__init__(scope, config.stack_name("detective"), env=config.cdk_env(), **kwargs)
        self.config = config
        self.graph = self.enable_detective()

    # Enables Amazon Detective with automatic findings ingestion
    def enable_detective(self) -> detective.CfnGraph:
        graph = detective.CfnGraph(
            self,
            "DetectiveGraph",
            auto_enable_members=False,
        )
        CfnOutput(
            self,
            self.config.make_name("DetectiveGraphArn"),
            value=graph.attr_arn,
            description="ARN of the Detective graph",
        )
        return graph