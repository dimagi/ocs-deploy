import json

from invoke import Context, Exit, task

from ocs_deploy.config import OCSConfig
from ocs_deploy.cli.tasks_aws_utils import (
    DEFAULT_PROFILE,
    PROFILE_HELP,
    _get_config,
    get_profile_and_auth,
)
from ocs_deploy.cli.tasks_utils import confirm


@task(
    help={
        "command": "Command to execute in the container. Defaults to '/bin/bash'",
        "service": "Service to connect to. One of [django, celery, beat, ec2tmp]. Defaults to 'django'",
    }
    | PROFILE_HELP,
    auto_shortflags=False,
)
def connect(c: Context, command="/bin/bash", service="django", profile=DEFAULT_PROFILE):
    """Connect to a running ECS container and execute the given command."""
    profile = get_profile_and_auth(c, profile)

    if service == "ec2tmp":
        config = _get_config(c)
        stack = config.stack_name(OCSConfig.EC2_TMP_STACK)
        name = config.make_name("TmpInstance")
        filters = f"--filters Name=tag:Name,Values={stack}/{name}"
        query = "--query 'Reservations[*].Instances[*].[InstanceId]'"
        result = c.run(
            f"aws ec2 describe-instances --output text {filters} {query}", hide=True
        )
        instances = result.stdout.strip().split()
        if not instances:
            raise Exit(
                f"No instances of {service} were found.",
                -1,
            )
        c.run(
            "aws ssm start-session --target " + instances[0],
            echo=True,
            pty=True,
        )

    else:
        _fargate_connect(c, command, service, profile)


def _fargate_connect(c: Context, command, service, profile):
    config = _get_config(c)
    cluster = config.make_name("Cluster")
    match service:
        case "django":
            service = config.make_name("Django")
            container = "web"
        case "celery":
            service = config.make_name("Celery")
            container = "celery-worker"
        case "beat":
            service = config.make_name("CeleryBeat")
            container = "celery-beat"
        case _:
            raise Exit(f"Unknown service '{service}'", -1)

    result = c.run(
        f"aws ecs list-tasks --cluster {cluster} --service {service} --profile {profile}",
        hide=True,
    )
    response = json.loads(result.stdout)
    tasks = response.get("taskArns", [])
    if not tasks:
        raise Exit(
            f"No tasks found for the '{service}' service in the '{cluster}' cluster.",
            -1,
        )

    fargate_task = tasks[0]
    c.run(
        f"aws ecs execute-command "
        f"--cluster {cluster} "
        f"--task {fargate_task} "
        f"--container {container} "
        f"--command {command} "
        f"--profile {profile} "
        f"--interactive",
        echo=True,
        pty=True,
    )


@task(
    help={
        "stacks": f"Comma-separated list of the stacks to deploy ({' | '.join(OCSConfig.ALL_STACKS)})",
        "verbose": "Enable verbose output",
        "maintenance": "Enable maintenance mode",
        "skip_approval": "Do not prompt for approval before deploying",
    }
    | PROFILE_HELP,
    auto_shortflags=False,
)
def deploy(
    c: Context,
    stacks=None,
    verbose=False,
    profile=DEFAULT_PROFILE,
    maintenance=False,
    skip_approval=False,
):
    """Deploy the specified stacks. If no stacks are specified, all stacks will be deployed."""
    profile = get_profile_and_auth(c, profile)

    config = _get_config(c)
    cmd = f"cdk deploy --profile {profile} --context ocs_env={config.environment}"
    if stacks:
        stacks = " ".join([config.stack_name(stack) for stack in stacks.split(",")])
        cmd += f" {stacks}"
    else:
        confirm("Deploy all stacks ?", _exit=True, exit_message="Aborted")
        cmd += " --all"
    if verbose:
        cmd += " --verbose"

    if maintenance:
        cmd += " --context maintenance_mode=true"

    cmd += " --require-approval " + ("never" if skip_approval else "any-change")
    cmd += " --progress events"
    c.run(cmd, echo=True, pty=True)


@task(
    help={
        "stacks": f"Comma-separated list of the stacks to deploy ({' | '.join(OCSConfig.ALL_STACKS)})",
        "verbose": "Enable verbose output",
        "maintenance": "Enable maintenance mode",
    }
    | PROFILE_HELP,
    auto_shortflags=False,
)
def diff(
    c: Context, stacks=None, verbose=False, profile=DEFAULT_PROFILE, maintenance=False
):
    """Deploy the specified stacks. If no stacks are specified, all stacks will be deployed."""
    profile = get_profile_and_auth(c, profile)

    config = _get_config(c)
    cmd = f"cdk diff --profile {profile} --context ocs_env={config.environment}"
    if stacks:
        stacks = " ".join([config.stack_name(stack) for stack in stacks.split(",")])
        cmd += f" {stacks}"
    else:
        cmd += " --all"
    if verbose:
        cmd += " --verbose"

    cmd += _check_maintenance_mode(maintenance)

    c.run(cmd, echo=True, pty=True)


@task(auto_shortflags=False)
def bootstrap(c: Context, profile=DEFAULT_PROFILE):
    """Bootstrap the AWS environment.

    This only needs to be run once per AWS account.
    """
    profile = get_profile_and_auth(c, profile)

    config = _get_config(c)
    c.run(
        f"cdk bootstrap --profile {profile} --context ocs_env={config.environment}",
        echo=True,
        pty=True,
    )


def _check_maintenance_mode(maintenance_mode):
    if maintenance_mode:
        confirm(
            "Maintenance mode is enabled. This will stop all service. Continue ?",
            _exit=True,
            exit_message="Aborted",
        )

    return " --context maintenance_mode=true" if maintenance_mode else ""
