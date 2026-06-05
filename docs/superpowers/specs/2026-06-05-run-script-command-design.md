# Design: `aws.run-script` — run one-off Django scripts remotely

**Date:** 2026-06-05
**Status:** Approved

## Goal

Run a local Python file in the deployed Django environment with a single command:

```bash
ocs aws.run-script path/to/script.py --env prod [--service django] [--profile X]
```

## Approach

ECS exec into the running container (same machinery as `aws.connect` / `aws.django-manage`), shipping the script inline since ECS exec has no file-copy support:

1. Read the local file and base64-encode it.
2. Resolve config and auth via `_get_config` / `get_profile_and_auth`.
3. Execute via `_fargate_connect` with:
   `bash -c "echo '<b64>' | base64 -d | python manage.py shell"`

The script runs top-level in full Django context (equivalent to piping it into `manage.py shell` locally). Output streams to the terminal.

## Details

- New `@task` in `ocs_deploy/cli/tasks_aws_utils.py`, alongside `django_manage`.
- `service` defaults to `django`; validated by the existing `_get_service_and_container`.
- Error with a clear message if the script file does not exist.

## Alternatives considered

- **S3 upload + download in container:** no size limit, but adds IAM/bucket coupling and cleanup. Rejected — base64 inline is sufficient for typical one-off scripts.
- **Interactive paste via `aws.connect`:** zero new code, but tedious and error-prone. Rejected.

## Limitations

- Very large scripts (tens of KB) may hit SSM command-size limits.

## Testing

No existing tests cover CLI tasks; verified manually, following the existing pattern.
