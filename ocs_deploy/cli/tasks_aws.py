from invoke import Context, task

from ocs_deploy.config import OCSConfig
from ocs_deploy.cli.tasks_aws_utils import (
    DEFAULT_PROFILE,
    NoQuote,
    PROFILE_HELP,
    _fargate_connect,
    _get_config,
    _get_service_and_container,
    _ssm_connect,
    aws_cli,
    get_profile_and_auth,
)
from ocs_deploy.cli.tasks_utils import confirm


STACKS_HELP = f"Comma-separated list of the stacks to deploy ({' | '.join(OCSConfig.ALL_STACKS)}). Defaults to ALL."
SERVICES_HELP = "Services to target [ALL, django, celery, beat]. Separate multiple with a comma. Defaults to 'ALL'"


@task(
    help={
        "command": "Command to execute in the container. Defaults to '/bin/bash'",
        "service": "Service to connect to. One of [django, celery, beat, ec2tmp]. Defaults to 'django'",
    }
    | PROFILE_HELP,
    auto_shortflags=False,
)
def connect(c: Context, command="bash -l", service="django", profile=DEFAULT_PROFILE):
    """Connect to a running ECS container and execute the given command."""
    config = _get_config(c)
    profile = get_profile_and_auth(c, profile)

    if service == "ec2tmp":
        _ssm_connect(c, config, command, service, profile)
    else:
        _fargate_connect(c, config, command, service, profile)


@task(
    help={
        "stacks": STACKS_HELP,
        "verbose": "Enable verbose output",
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
    skip_approval=False,
):
    """Deploy the specified stacks. If no stacks are specified, all stacks will be deployed."""
    profile = get_profile_and_auth(c, profile)

    config = _get_config(c)
    cmd = "cdk deploy"
    if stacks:
        stacks = " ".join([config.stack_name(stack) for stack in stacks.split(",")])
        cmd += f" {stacks} --exclusively"
    else:
        confirm("Deploy all stacks ?", _exit=True, exit_message="Aborted")
        cmd += " --all"
    if verbose:
        cmd += " --verbose"

    cmd += f" --profile {profile} --context ocs_env={config.environment}"
    cmd += " --require-approval " + ("never" if skip_approval else "any-change")
    cmd += " --progress events"
    c.run(cmd, echo=True, pty=True)


@task(
    help={
        "stacks": STACKS_HELP,
        "verbose": "Enable verbose output",
    }
    | PROFILE_HELP,
    auto_shortflags=False,
)
def diff(
    c: Context,
    stacks=None,
    verbose=False,
    profile=DEFAULT_PROFILE,
):
    """Generate of list of changes to be deployed."""
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
    c.run(cmd, echo=True, pty=True)


@task(
    help={"services": SERVICES_HELP},
    auto_shortflags=False,
)
def restart(c: Context, services="ALL", profile=DEFAULT_PROFILE):
    """Restart ECS services."""
    config = _get_config(c)
    profile = get_profile_and_auth(c, profile)
    _update_services(c, config, services, profile, "restart", force=True)


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
    _update_services(c, config, services, profile, action, extra_args=extra_args)


def _update_services(
    c: Context, config, services, profile, action, force=True, extra_args=None
):
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
            force_new_deployment=force,
        )
        if extra_args:
            command += " " + extra_args
        c.run(command, echo=True, pty=True, hide="out")

    c.run(
        aws_cli(
            "ecs wait services-stable",
            profile,
            cluster=cluster,
            services=NoQuote(" ".join(service_names)),
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


def _get_services(services):
    if services == "ALL":
        services = ["django", "celery", "beat"]
    else:
        services = [s.strip() for s in services.split(",")]
    return services
