# Inbound Email Infrastructure — Design Document

**Date:** 2026-04-28
**Status:** Draft
**Companion app PR:** [dimagi/open-chat-studio#3175](https://github.com/dimagi/open-chat-studio/pull/3175)

## Overview

Add AWS infrastructure to support inbound email for Open Chat Studio. The OCS app PR #3175 adds an email messaging channel that consumes `anymail.signals.inbound`; this design wires up the AWS side so SES receives mail for one or more OCS-controlled subdomains and pushes it to the Django app's anymail webhook.

A single CDK environment supports a fixed list of inbound subdomains (e.g. `chat.openchatstudio.com` plus a second TBD subdomain). All domains share one receipt rule set, one SNS topic, one S3 bucket, and one webhook secret.

## Goals & Non-Goals

### Goals

- Support a fixed, deploy-time-configured list of OCS-owned inbound email subdomains.
- Deliver inbound mail to the existing Django ALB via the anymail SES webhook URL.
- Support attachments up to 10 MB (full SES inbound limit is 40 MB).
- Each inbound domain is a verified SES `EmailIdentity` with DKIM, so the OCS app can send replies from any of those domains via per-channel `from_address`.
- Auto-confirm the SNS HTTPS subscription via Django's IAM role (no manual click).
- Surface MX + DKIM DNS records as `CfnOutput`s (DNS is managed manually by Dimagi, same as today).

### Non-Goals

- Customer-owned domains (would require runtime SES API calls and per-customer DKIM coordination — Phase 2 of the OCS PR's design).
- IMAP / Gmail API / Microsoft Graph integration.
- Outbound email infrastructure changes (SES sending is already in place).
- OCS app-side attachment processing (separate PR; the infra makes it possible, but the Django code in PR #3175 currently ignores attachments).
- Per-environment receipt-action variation (always S3 + SNS).

## Architecture

```
                ┌────────────────────────────────────┐
                │ DNS (manually managed by Dimagi)   │
                │  MX  chat.openchatstudio.com       │
                │      → inbound-smtp.us-east-1...   │
                │  MX  chat2.openchatstudio.com  →…  │
                │  CNAME DKIM ×3 per domain          │
                └────────────────┬───────────────────┘
                                 │ inbound mail
                                 ▼
   ┌───────── SES (us-east-1) ──────────────────────────────────┐
   │  EmailIdentity  chat.openchatstudio.com  (outbound DKIM)    │
   │  EmailIdentity  chat2.openchatstudio.com (outbound DKIM)    │
   │                                                             │
   │  ReceiptRuleSet  ocs-<env>-inbound  (must be activated)     │
   │   └─ ReceiptRule "ocs-<env>-deliver"                        │
   │       recipients: [chat..., chat2..., …]                    │
   │       actions:                                              │
   │         1. S3Action(bucket=ocs-<env>-ses-inbound-mail,      │
   │                     prefix="inbound/")                      │
   │         2. SnsAction(topic=ocs-<env>-ses-inbound, Base64)   │
   └────────────────────┬────────────────────────────────────────┘
                        │ SNS notification (event JSON; 15s budget)
                        ▼
       SNS Topic: ocs-<env>-ses-inbound
                        │ HTTPS subscription
                        ▼
   https://anymail:<pwd>@<DOMAIN_NAME>/anymail/amazon_ses/inbound/
                        │
                        ▼
                 Django ALB → anymail handler
                 ─ validates basic auth via ANYMAIL_WEBHOOK_SECRET
                 ─ fetches raw mail from S3 via task IAM role
                 ─ fires anymail.signals.inbound
                 ─ Celery task → channels_v2 pipeline → reply
```

**Key properties:**

- New CDK stack `SesInboundStack`. Independent deploy/destroy.
- `DomainStack` extended to loop over `[primary] + extras` for `EmailIdentity` creation.
- One receipt rule covers all domains via its `recipients` list (well under the 200-rules-per-set limit).
- S3 + SNS delivery (always — supports up to 10 MB attachments and beyond).
- New Secrets Manager secret `anymail-webhook-secret` is created by Secrets Manager auto-generation, referenced as a Django env var **and** as basic-auth credentials in the SNS HTTPS subscription URL via a CDK `SecretValue` dynamic reference.
- DNS records (MX + DKIM CNAMEs) are emitted as `CfnOutput`s for Dimagi to apply manually — same pattern as the existing `DomainStack`.

## Components & Files Changed

| File | Change |
|---|---|
| `ocs_deploy/config.py` | Add `email_domain` (existing primary), `email_inbound_domains: list[str]` (CSV-parsed from `EMAIL_INBOUND_DOMAINS`, default `[]`), and helper `all_inbound_domains` returning `[email_domain, *email_inbound_domains]`. Add a new `make_secret_name("anymail-webhook-secret")` accessor. Add `SES_INBOUND_STACK = "ses-inbound"` constant and entry in `ALL_STACKS`. |
| `ocs_deploy/domains.py` | Replace the single `create_email_identity` call with a loop over `config.all_inbound_domains`. Each domain gets its own `ses.EmailIdentity` (sharing the existing `Default` configuration set) and three DKIM `CfnOutput`s, named/exported with the domain so multiple identities don't collide. Keep ACM cert logic untouched. |
| `ocs_deploy/ses_inbound.py` | **New stack** `SesInboundStack`. Creates: private S3 bucket with `BlockPublicAccess.BLOCK_ALL` and a 7-day expiration lifecycle rule on `inbound/`; an SES bucket policy allowing `s3:PutObject` from `ses.amazonaws.com` scoped via `aws:SourceAccount` and `aws:SourceArn`; an SNS `Topic`; the `anymail-webhook-secret` Secrets Manager secret with auto-generated value (excluded chars: `:/@`); a `ses.ReceiptRuleSet` with one `ReceiptRule` whose `recipients=config.all_inbound_domains` and ordered `[S3Action, SnsAction(encoding=Base64)]`; an `sns.Subscription` of type `HTTPS` whose endpoint is built from the primary `DOMAIN_NAME` and `cdk.SecretValue.secrets_manager(...)`. Outputs: bucket name, topic ARN, receipt-rule-set name, and the exact `aws ses set-active-receipt-rule-set` command. |
| `ocs_deploy/secrets.yml` | Add `anymail-webhook-secret` as a managed secret (auto-generated value at create time). |
| `ocs_deploy/fargate.py` | `secrets_dict`: add `ANYMAIL_WEBHOOK_SECRET` referencing the new Secrets Manager secret. `task_role`: add `sns:ConfirmSubscription` on the SES inbound topic ARN, and `s3:GetObject` on `<bucket-arn>/inbound/*`. Constructor takes a new `ses_inbound_stack` argument so cross-stack ARN references work without manual exports. |
| `app.py` | Instantiate `SesInboundStack` after `DomainStack`. Pass it into `FargateStack`. |
| `ocs_deploy/cli/aws.py` | Register `ses-inbound` as a deployable stack. |
| `.env.example`, `.env.dev`, `.env.prod` | Add `EMAIL_INBOUND_DOMAINS=` (default empty). No new env var beyond this; `EMAIL_CHANNEL_DOMAIN` is **not** required (the OCS PR removed it). |
| `README.md` | New "Inbound Email" subsection covering: deploy ordering, the manual `aws ses set-active-receipt-rule-set` step, MX records to add per domain, DKIM CNAMEs to add per domain, and how to send a test email. |

### Why a single receipt rule with multiple recipients

SES allows up to 200 rules per rule set, and each rule can list many recipient domains. One rule with `recipients=[d1, d2, d3]` is functionally identical to three rules each with one recipient — but is simpler to manage and faster to evaluate. Use a single rule unless we ever need per-domain action variation (we don't).

### Activating the receipt rule set

CDK's `ses.ReceiptRuleSet` does not call `SetActiveReceiptRuleSet`; only one rule set per region can be active at a time. Two options:

1. L1 `CfnReceiptRuleSet` plus a custom-resource Lambda that activates on create / reverts on delete.
2. Document a manual `aws ses set-active-receipt-rule-set` step.

This design uses **option 2**. Reasons: no Lambda to maintain; no risk of clobbering an active rule set in a region the team may not own exclusively; one-time operator action that's easy to verify. The `SesInboundStack` emits the exact command as a `CfnOutput` for copy-paste.

## Data Flow

### Deploy time (per environment)

1. `ocs --env <env> aws.deploy --stacks domains` — creates an `EmailIdentity` per domain. Stack outputs all DKIM CNAMEs grouped by domain.
2. Operator adds MX + DKIM records to DNS for each domain (manual). MX target: `inbound-smtp.us-east-1.amazonaws.com`.
3. `ocs --env <env> secrets.create-missing` — creates `anymail-webhook-secret` with an auto-generated value.
4. `ocs --env <env> aws.deploy --stacks ses-inbound` — creates the bucket, topic, receipt rule set, rule, and HTTPS subscription. CFN resolves the secret and embeds it into the SNS endpoint URL.
5. Operator runs the `aws ses set-active-receipt-rule-set` command from the stack output.
6. `ocs --env <env> aws.deploy --stacks django` — Django picks up `ANYMAIL_WEBHOOK_SECRET` and the new IAM permissions; anymail auto-confirms the SNS subscription on the next webhook hit.

### Runtime (per inbound email)

1. SES receives mail to a recipient that matches the rule's `recipients` list.
2. SES executes the rule actions in order: write the raw MIME to `s3://ocs-<env>-ses-inbound-mail/inbound/<event-id>`, then publish the SES event JSON (Base64-encoded) to the SNS topic.
3. SNS POSTs to the Anymail webhook URL with HTTP basic auth.
4. Django (anymail) validates basic auth against `ANYMAIL_WEBHOOK_SECRET`, fetches the raw mail from S3 via the task IAM role, parses MIME, fires `anymail.signals.inbound`.
5. The OCS signal handler queues a Celery task and returns 200 OK (well under SNS's 15-second budget for ≤10 MB messages).
6. Celery worker runs the existing `channels_v2` pipeline; `EmailSender` sends the reply via SES (task role already has `ses:SendEmail`/`SendRawEmail`).

## Cross-Cutting Concerns

### Failure modes

| Mode | Mitigation |
|---|---|
| Webhook unreachable for >1 minute | SNS gives up; mail is lost. ALB has 2 min-capacity tasks; SES inbound retries are not configurable. Acceptable risk for v1; documented in README. |
| Receipt rule set not activated | Mail bounces or vanishes. Operator step is explicit + the activation command is a stack output. |
| MX missing for a domain | Mail never reaches SES. Stack output checklist enumerates MX targets per domain. |
| Domain in `EMAIL_INBOUND_DOMAINS` but DKIM not yet in DNS | SES inbound still works (verification is for sending). Outbound replies from that domain will fail until DKIM is in DNS. README notes this. |
| S3 fetch slow inside signal receiver | 15-second SNS budget could be exceeded for very large messages. For ≤10 MB this is comfortable; if larger messages are ever needed, switch the OCS app to defer the fetch into the Celery task. Out of scope for this design. |
| SNS subscription expires after 3 days unconfirmed | Auto-confirmation via `sns:ConfirmSubscription` on the Django task role. If the role is missing it for any reason, the operator can confirm via the AWS console subscription URL. |

### IAM scope

- Task role gets `sns:ConfirmSubscription` on the specific topic ARN (not `*`).
- Task role gets `s3:GetObject` on `<bucket-arn>/inbound/*` (not the whole bucket).
- SES bucket-policy `s3:PutObject` is conditioned on `aws:SourceAccount = <account>` and `aws:SourceArn = <receipt-rule-arn>` per AWS guidance.

### Security

- The S3 bucket has `BlockPublicAccess.BLOCK_ALL` and is otherwise private.
- The webhook secret is auto-generated by Secrets Manager (alphanumeric, 32+ chars; excluded chars `:`, `/`, `@` to keep the basic-auth URL parseable).
- SNS notifications travel over HTTPS with basic auth. Without correct basic auth, anymail returns 401 — same protection as the existing tracking webhook (if any).
- CFN dynamic references for the secret resolve at deploy time; the resolved value is visible in the SNS subscription's endpoint URL within the AWS console (same trust boundary as the secret itself).

## Configuration Surface

| Source | Key | Example | Required? |
|---|---|---|---|
| `.env.<env>` | `EMAIL_DOMAIN` | `chat.openchatstudio.com` | Yes (existing) |
| `.env.<env>` | `EMAIL_INBOUND_DOMAINS` | `chat2.openchatstudio.com,chat3.openchatstudio.com` | No (default empty) |
| Secrets Manager | `ocs/<env>/anymail-webhook-secret` | auto-generated | Yes (managed) |
| Django env (auto) | `ANYMAIL_WEBHOOK_SECRET` | from Secrets Manager | Yes |

No `EMAIL_CHANNEL_DOMAIN` env var — the OCS PR removed that requirement. The Django app derives reply-from addresses from per-channel config.

## Testing

### CDK synth tests (`ocs_deploy/tests/`)

- `test_ses_inbound_stack.py`: with `EMAIL_DOMAIN=c.com` and `EMAIL_INBOUND_DOMAINS="a.com,b.com"`, assert the synthesized template has:
  - Exactly one `AWS::SES::ReceiptRuleSet`.
  - Exactly one `AWS::SES::ReceiptRule` with `Recipients=[c.com, a.com, b.com]`.
  - The rule's `Actions[0]` is an `S3Action` with the bucket reference and `inbound/` prefix.
  - The rule's `Actions[1]` is an `SNSAction` with `Encoding=Base64`.
  - The bucket has `PublicAccessBlockConfiguration` set to all-true and a `LifecycleConfiguration` with a 7-day expiration on `inbound/`.
  - The bucket policy `Statement` allows `s3:PutObject` to `Service=ses.amazonaws.com` with both `aws:SourceAccount` and `aws:SourceArn` conditions.
  - One HTTPS `AWS::SNS::Subscription` whose `Endpoint` contains `/anymail/amazon_ses/inbound/`.
- `test_domains_stack.py`: assert the loop produces N `EmailIdentity` resources and N×3 DKIM `CfnOutput`s when N domains are configured.
- `test_fargate.py`: assert the task role has `sns:ConfirmSubscription` scoped to the topic ARN and `s3:GetObject` scoped to `<bucket-arn>/inbound/*`. Assert `ANYMAIL_WEBHOOK_SECRET` is present in the task secrets dict.

### Manual post-deploy validation (in README runbook)

1. `aws ses describe-active-receipt-rule-set` returns the new rule set name.
2. `dig MX chat.openchatstudio.com` returns the SES inbound endpoint.
3. Send a test email to `support@chat.openchatstudio.com`. Check:
   - CloudWatch SES metrics show a delivery to S3.
   - `aws s3 ls s3://ocs-<env>-ses-inbound-mail/inbound/` shows the object.
   - Django CloudWatch logs show `anymail.signals.inbound` firing.
4. Repeat with a 10 MB attachment to confirm the S3 path works end-to-end.

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Domain-list scope | Fixed list of OCS-owned subdomains, configured at deploy time. |
| 2 | Receipt action | Always S3 + SNS (supports ≤10 MB attachments). |
| 3 | Stack layout | New dedicated `SesInboundStack`; `DomainStack` extended to loop over the domain list. |
| 4 | EmailIdentity per domain | Yes — every inbound domain also gets DKIM-verified outbound identity. |
| 5 | Config format | CSV env var `EMAIL_INBOUND_DOMAINS`. |
| 6 | `EMAIL_CHANNEL_DOMAIN` env var | Not added — removed from the OCS PR. |
| 7 | Receipt-rule-set activation | Manual `aws ses set-active-receipt-rule-set`, surfaced as a stack output. |
| 8 | SNS subscription auto-confirm | `sns:ConfirmSubscription` on the Django task role, scoped to the topic ARN. |
| 9 | S3 bucket lifecycle | 7-day expiration on `inbound/`. |

## Open Questions

| # | Question | Notes |
|---|---|---|
| A | What is the second inbound subdomain? | Listed as TBD in the OCS PR design doc. Doesn't block this design — the env var is empty by default and accepts any list. |
