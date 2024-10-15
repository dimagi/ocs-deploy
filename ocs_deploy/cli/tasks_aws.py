import json

from invoke import Context, Exit, task

from ocs_deploy.config import OCSConfig
from ocs_deploy.cli.tasks_aws_utils import (
    DEFAULT_PROFILE,
    PROFILE_HELP,
    _get_config,
    aws_cli,
    get_profile_and_auth,
)
from ocs_deploy.cli.tasks_utils import confirm


SERVICES_HELP = "Services to target [ALL, django, celery, beat]. Separate multiple with a comma. Defaults to 'ALL'"


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
    config = _get_config(c)
    profile = get_profile_and_auth(c, profile)

    if service == "ec2tmp":
        stack = config.stack_name(OCSConfig.EC2_TMP_STACK)
        name = config.make_name("TmpInstance")
        filters = f"Name=tag:Name,Values={stack}/{name}"
        query = "'Reservations[*].Instances[*].[InstanceId]'"
        result = c.run(
            aws_cli("ec2 describe-instances", profile, filters=filters, query=query),
            hide=True,
        )
        instances = result.stdout.strip().split()
        if not instances:
            raise Exit(
                f"No instances of {service} were found.",
                -1,
            )
        c.run(
            aws_cli("ssm start-session", profile, target=instances[0]),
            echo=True,
            pty=True,
        )

    else:
        _fargate_connect(c, command, service, profile)


def _fargate_connect(c: Context, command, service, profile):
    config = _get_config(c)
    cluster = config.make_name("Cluster")
    service, container = _get_service_and_container(config, service)

    result = c.run(
        aws_cli("ecs list-tasks", profile, service=service, cluster=cluster),
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
        aws_cli(
            "ecs execute-command",
            profile,
            cluster=cluster,
            task=fargate_task,
            container=container,
            command=command,
            interactive=True,
        ),
        echo=True,
        pty=True,
    )


def _get_service_and_container(config, service):
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
    return service, container


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


@task(
    help={"services": SERVICES_HELP},
    auto_shortflags=False,
)
def restart(c: Context, services="ALL", profile=DEFAULT_PROFILE):
    """Restart an ECS service."""
    config = _get_config(c)
    profile = get_profile_and_auth(c, profile)
    _update_services(c, config, services, profile, "restart")


@task(
    name="maintenance:on",
    help={"services": SERVICES_HELP},
    auto_shortflags=False,
)
def maintenance_on(c: Context, services="ALL", profile=DEFAULT_PROFILE):
    """Enable maintenance mode."""
    maintenance(c, True, services, profile)


@task(
    name="maintenance:off",
    help={"services": SERVICES_HELP},
    auto_shortflags=False,
)
def maintenance_off(c: Context, services="ALL", profile=DEFAULT_PROFILE):
    """Disable maintenance mode."""
    maintenance(c, False, services, profile)


def maintenance(c: Context, enable, services="ALL", profile=DEFAULT_PROFILE):
    config = _get_config(c)
    profile = get_profile_and_auth(c, profile)
    action = "stop" if enable else "start"
    extra_args = "--desired-count 0" if enable else "--desired-count 1"
    _update_services(c, config, services, profile, action, extra_args)


def _update_services(c: Context, config, services, profile, action, extra_args=None):
    services = _get_services(services)

    confirm(
        f"This will {action} the following services: {', '.join(services)}. Continue ?",
        _exit=True,
        exit_message="Aborted",
    )

    cluster = config.make_name("Cluster")
    service_names = []
    for service in services:
        service_name, _ = _get_service_and_container(config, service)
        service_names.append(service_name)
        command = aws_cli(
            "ecs update-service",
            profile,
            service=service_name,
            cluster=cluster,
            force_new_deployment=True,
        )
        if extra_args:
            command += " " + extra_args
        c.run(command, echo=True, pty=True, hide="out")

    c.run(
        aws_cli(
            "ecs wait services-stable",
            profile,
            cluster=cluster,
            services=" ".join(service_names),
        ),
        echo=True,
        pty=True,
    )


@task(auto_shortflags=False)
def bootstrap(c: Context, profile=DEFAULT_PROFILE):
    """Bootstrap the AWS environment.

    This only needs to be run once per AWS account.
    """
    config = _get_config(c)
    profile = get_profile_and_auth(c, profile)

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


def _get_services(services):
    if services == "ALL":
        services = ["django", "celery", "beat"]
    else:
        services = [s.strip() for s in services.split(",")]
    return services
