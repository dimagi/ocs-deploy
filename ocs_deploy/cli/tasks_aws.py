import json

from invoke import Context, Exit, task
from termcolor import cprint

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
    help=PROFILE_HELP,
    auto_shortflags=False,
)
def migrate(c: Context, profile=DEFAULT_PROFILE):
    """Run Django migrations as a one-off ECS task.

    This runs the migration task definition and waits for it to complete.
    Use this before deploying new code or when setting up a new environment.
    """
    config = _get_config(c)
    profile = get_profile_and_auth(c, profile)

    # Get stack outputs for network configuration
    stack_name = config.stack_name(OCSConfig.DJANGO_STACK)
    cprint(f"Getting network configuration from stack: {stack_name}", color="blue")

    result = c.run(
        aws_cli(
            "cloudformation describe-stacks",
            profile,
            stack_name=stack_name,
            query="Stacks[0].Outputs",
            output="json",
        ),
        hide=True,
    )
    outputs = json.loads(result.stdout)

    # Extract outputs by suffix
    def get_output(suffix):
        for output in outputs:
            if output["OutputKey"].endswith(suffix):
                return output["OutputValue"]
        raise Exit(f"Could not find stack output ending with '{suffix}'", -1)

    subnets = get_output("PrivateSubnets")
    security_group = get_output("ServiceSecurityGroup")
    task_definition = get_output("MigrationTaskArn")
    cluster = config.make_name("Cluster")

    cprint(f"Running migration task: {task_definition}", color="blue")

    # Run the migration task
    result = c.run(
        aws_cli(
            "ecs run-task",
            profile,
            cluster=cluster,
            task_definition=task_definition,
            launch_type="FARGATE",
            network_configuration=f"awsvpcConfiguration={{subnets=[{subnets}],securityGroups=[{security_group}],assignPublicIp=DISABLED}}",
            query="tasks[0].taskArn",
            output="text",
        ),
        hide=True,
    )
    task_arn = result.stdout.strip()
    task_id = task_arn.split("/")[-1]
    cprint(f"Started migration task: {task_id}", color="green")

    # Wait for task to complete
    cprint("Waiting for migration to complete...", color="blue")
    c.run(
        aws_cli(
            "ecs wait tasks-stopped",
            profile,
            cluster=cluster,
            tasks=task_arn,
        ),
        hide=True,
    )

    # Show logs
    log_group = config.make_name(config.LOG_GROUP_DJANGO_MIGRATIONS)
    log_stream = f"{config.make_name('migrate')}/migrate/{task_id}"
    cprint("\n--- Migration logs ---", color="blue")
    result = c.run(
        aws_cli(
            "logs get-log-events",
            profile,
            log_group_name=log_group,
            log_stream_name=log_stream,
            query="events[*].message",
            output="json",
        ),
        warn=True,
        hide=True,
    )
    for line in json.loads(result.stdout):
        print(line)
    cprint("--- End logs ---\n", color="blue")

    # Check exit code
    result = c.run(
        aws_cli(
            "ecs describe-tasks",
            profile,
            cluster=cluster,
            tasks=task_arn,
            query="tasks[0].containers[0].exitCode",
            output="text",
        ),
        hide=True,
    )
    exit_code = result.stdout.strip()

    if exit_code != "0":
        cprint(f"Migration failed with exit code: {exit_code}", color="red")
        raise Exit("Migration failed", -1)

    cprint("Migration completed successfully!", color="green")


@task(
    help={
        "stacks": STACKS_HELP,
        "verbose": "Enable verbose output",
        "skip_approval": "Do not prompt for approval before deploying",
        "with_dependencies": "Include dependent stacks. This is usually only needed when setting up the "
        "resources the first time.",
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
    with_dependencies=False,
):
    """Deploy the specified stacks. If no stacks are specified, all stacks will be deployed."""
    args = " --progress events"
    args += " --require-approval " + ("never" if skip_approval else "any-change")
    if not stacks and not with_dependencies:
        args += " --exclusively"
    _run_cdk_stack_command(c, "deploy", stacks, verbose, profile, extra_args=args)


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
    _run_cdk_stack_command(c, "diff", stacks, verbose, profile)


@task(
    help={
        "stacks": STACKS_HELP,
        "verbose": "Enable verbose output",
    }
    | PROFILE_HELP,
    auto_shortflags=False,
)
def destroy(
    c: Context,
    stacks=None,
    verbose=False,
    profile=DEFAULT_PROFILE,
):
    """Destroy stacks"""
    stacks_msg = stacks or "all stacks"
    confirm(
        f"Are you sure you want to destroy {stacks_msg}? This cannot be undone.",
        _exit=True,
        exit_message="Aborted",
    )
    args = "--force"
    _run_cdk_stack_command(c, "destroy", stacks, verbose, profile, extra_args=args)


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
    _run_cdk(c, "bootstrap", profile=profile)


@task(auto_shortflags=False)
def list_stacks(c: Context, profile=DEFAULT_PROFILE):
    """Bootstrap the AWS environment.

    This only needs to be run once per AWS account.
    """
    _run_cdk(c, "list", profile=profile)


def _get_services(services):
    if services == "ALL":
        services = ["django", "celery", "beat"]
    else:
        services = [s.strip() for s in services.split(",")]
    return services


def _run_cdk_stack_command(
    c: Context,
    command,
    stacks=None,
    verbose=False,
    profile=DEFAULT_PROFILE,
    extra_args=None,
):
    config = _get_config(c)
    extra_args = extra_args or ""
    if stacks:
        stacks = " ".join([config.stack_name(stack) for stack in stacks.split(",")])
        extra_args += f" {stacks}"
    else:
        confirm(f"Run '{command}' on all stacks ?", _exit=True, exit_message="Aborted")
        extra_args += " --all"

    _run_cdk(c, command, verbose, profile, extra_args=extra_args)


def _run_cdk(
    c: Context, command, verbose=False, profile=DEFAULT_PROFILE, extra_args=None
):
    profile = get_profile_and_auth(c, profile)

    config = _get_config(c)
    cmd = f"cdk {command} --profile {profile} --context ocs_env={config.environment}"
    if verbose:
        cmd += " --verbose"
    if extra_args:
        cmd += f" {extra_args}"
    c.run(cmd, echo=True, pty=True)
