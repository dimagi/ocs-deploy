import json

import aws_cdk as cdk
from aws_cdk.assertions import Template

from ocs_deploy.ecr import EcrStack


def _lifecycle_rules(ocs_config):
    app = cdk.App()
    stack = EcrStack(app, ocs_config)
    template = Template.from_stack(stack)
    repo = list(template.find_resources("AWS::ECR::Repository").values())[0]
    policy = repo["Properties"]["LifecyclePolicy"]["LifecyclePolicyText"]
    return json.loads(policy)["rules"]


def test_untagged_images_outlive_a_pinned_deployment(ocs_config):
    """ECS pins `latest` to a digest, so untagged images must not be pruned quickly."""
    rules = _lifecycle_rules(ocs_config)
    untagged = [r for r in rules if r["selection"]["tagStatus"] == "untagged"]
    assert len(untagged) == 1
    assert untagged[0]["selection"]["countNumber"] >= 30


def test_image_count_retains_more_than_a_few_deploys(ocs_config):
    rules = _lifecycle_rules(ocs_config)
    any_status = [r for r in rules if r["selection"]["tagStatus"] == "any"]
    assert len(any_status) == 1
    assert any_status[0]["selection"]["countNumber"] >= 20
