import json

from invoke import Context, Exit, task

from ocs_deploy.config import OCSConfig
from tasks_aws_utils import (
    DEFAULT_PROFILE,
    PROFILE_HELP,
    _get_config,
    get_profile_and_auth,
)
from tasks_utils import confirm


@task(
    help={
        "command": "Command to execute in the container. Defaults to '/bin/bash'",
        "service": "Service to connect to. One of [django, celery, beat]. Defaults to 'django'",
    }
    | PROFILE_HELP
)
def connect(c: Context, command="/bin/bash", service="django", profile=DEFAULT_PROFILE):
    """Connect to a running ECS container and execute the given command."""
    profile = get_profile_and_auth(c, profile)

    config = _get_config()
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
    }
    | PROFILE_HELP
)
def deploy(c: Context, stacks=None, verbose=False, profile=DEFAULT_PROFILE):
    """Deploy the specified stacks. If no stacks are specified, all stacks will be deployed."""
    profile = get_profile_and_auth(c, profile)

    config = _get_config()
    cmd = f"cdk deploy --profile {profile}"
    if stacks:
        stacks = " ".join([config.stack_name(stack) for stack in stacks.split(",")])
        cmd += f" {stacks}"
    else:
        confirm("Deploy all stacks ?", _exit=True, exit_message="Aborted")
        cmd += " --all"
    if verbose:
        cmd += " --verbose"

    cmd += " --progress events"
    c.run(cmd, echo=True, pty=True)
