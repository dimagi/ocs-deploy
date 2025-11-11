import json
import os
import shlex

from invoke import Context, Exit, task
from termcolor import cprint

from ocs_deploy.config import OCSConfig

DEFAULT_PROFILE = os.getenv("AWS_PROFILE")

PROFILE_HELP = {
    "profile": "AWS profile to use for deployment. Will read from AWS_PROFILE env var if not set."
}


@task(name="login", help=PROFILE_HELP)
def aws_login(c: Context, profile=DEFAULT_PROFILE):
    """Login to AWS SSO."""
    _get_config(c)
    if not profile:
        env = c.config.environment
        cprint(
            "AWS profile not set. You can pass it via '--profile' or the AWS_PROFILE env var.",
            color="light_grey",
        )
        default = f"ocs-{env}"
        profile = input(f"Enter profile: [Press enter to use '{default}'] ") or default

    cprint(f"Using AWS profile: {profile}", color="blue")
    result = c.run(aws_cli("sso login", profile), echo=True)
    return result.ok


@task
def django_manage(c: Context, command, profile=DEFAULT_PROFILE):
    """Run a Django management command on the Django Fargate service.

    This is an alias of:

        aws.connect --service django --command "python manage.py {command}"
    """
    _shell(c, profile, command)


def _shell(c: Context, profile=DEFAULT_PROFILE, mgmt_command="shell"):
    config = _get_config(c)
    profile = get_profile_and_auth(c, profile)
    _fargate_connect(c, config, f"python manage.py {mgmt_command}", "django", profile)


@task(
    help={
        "service": "The service to retrieve logs for. One of 'django', 'celery', or 'beat'.",
        "follow": "Follow the logs.",
        "since": "How far back to fetch logs. Ex: 1h, 30m, 10s.",
        **PROFILE_HELP,
    }
)
def tail(
    c: Context, service="django", follow=False, since=None, profile=DEFAULT_PROFILE
):
    """Tail the logs of a Fargate service."""
    config = _get_config(c)
    profile = get_profile_and_auth(c, profile)
    log_group = {
        "django": config.LOG_GROUP_DJANGO,
        "celery": config.LOG_GROUP_CELERY,
        "beat": config.LOG_GROUP_BEAT,
    }[service]
    log_group_name = config.make_name(log_group)
    cmd = f"aws logs tail {log_group_name} --profile {profile}"
    if follow:
        cmd += " --follow"
    if since:
        cmd += f" --since {since}"
    c.run(cmd, echo=True, pty=True)


def _check_credentials(c: Context, profile: str):
    result = c.run(aws_cli("sts get-caller-identity", profile), warn=True, hide=True)
    return result.ok


def get_profile_and_auth(c: Context, profile):
    if not profile:
        env = c.config.environment
        cprint(
            "AWS profile not set. You can pass it via '--profile' or the AWS_PROFILE env var.",
            color="light_grey",
        )
        default = f"ocs-{env}"
        profile = input(f"Enter profile: [Press enter to use '{default}'] ") or default
        os.environ["AWS_PROFILE"] = profile

    cprint(f"Using AWS profile: {profile}", color="blue")
    if not _check_credentials(c, profile):
        if not aws_login(c, profile):
            raise Exit("Failed to login", -1)

    return profile


def _get_config(c: Context):
    env = c.config.environment
    if not env:
        raise Exit(
            "No environment specified. Use '--env' or the 'OCS_DEPLOY_ENV' environment variable.",
            -1,
        )
    cprint(f"Using environment: {env}", color="blue")
    return OCSConfig(env)


def aws_cli(cmd, profile, **kwargs):
    """Generate an AWS CLI command."""
    args = ""
    for k, v in kwargs.items():
        k = k.replace("_", "-")
        if v is True:
            args += f" --{k}"
        elif v is False:
            continue
        else:
            if isinstance(v, NoQuote):
                value = v
            else:
                value = shlex.quote(v)
            args += f" --{k} {value}"
    return f"aws --no-cli-pager {cmd} --profile={profile} {args}"


class NoQuote(str):
    """A string that should not be quoted when passed to a shell command."""

    pass


def _ssm_connect(c, config, command, service, profile):
    stack = config.stack_name(OCSConfig.EC2_TMP_STACK)
    name = config.make_name("TmpInstance")
    filters = f"Name=tag:Name,Values={stack}/{name}"
    query = "Reservations[*].Instances[*].[InstanceId]"
    result = c.run(
        aws_cli(
            "ec2 describe-instances",
            profile,
            filters=filters,
            query=query,
            output="text",
        ),
        hide=True,
    )
    instances = result.stdout.strip().split()
    if not instances:
        raise Exit(
            f"No instances of {service} were found.",
            -1,
        )
    c.run(
        aws_cli(
            "ssm start-session",
            profile,
            target=instances[0],
            document_name="AWS-StartInteractiveCommand",
            parameters=f"command={command}",
        ),
        echo=True,
        pty=True,
    )


def _fargate_connect(c: Context, config, command, service, profile):
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
