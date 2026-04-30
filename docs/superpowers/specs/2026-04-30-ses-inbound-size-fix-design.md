# SES Inbound Size-Limit Fix — Design Document

**Date:** 2026-04-30
**Status:** Draft
**Related:** [`docs/superpowers/specs/2026-04-28-inbound-email-design.md`](./2026-04-28-inbound-email-design.md) (original inbound-email design)

## Problem

Inbound emails to `chat.openchatstudio.com` larger than ~150 KB bounce back to the sender with `"Message length exceeds limit set by recipient"`. A 500 KB PDF attachment reproduced the issue.

Root cause: in `ocs_deploy/ses_inbound.py`, the receipt rule has two actions — `S3` (writes raw MIME to a bucket) and `Sns(encoding=Base64)` (publishes the full base64-encoded email body inline to SNS). AWS SES caps the SNS-action payload at 150 KB; anything larger is bounced before either action runs to completion.

The original inbound-email design (2026-04-28) targeted attachments up to 10 MB and described the data flow as "Django (anymail) … fetches the raw mail from S3 via the task IAM role." The inline-SNS encoding contradicted that intent.

## Goal

Restore the original 10 MB target by removing the inline-SNS bottleneck. No support-ceiling change, no architectural redesign, no app-side change.

## Non-Goals

- Raising the support ceiling beyond 10 MB.
- Replacing the SNS-and-Lambda forwarder pattern with S3 event notifications or another transport.
- Application-side or anymail configuration changes.
- DNS, secret, or IAM changes.

## Approach

Replace the two-action receipt rule with a single S3 action that uses its built-in optional `topic` parameter to publish a metadata-only notification. Anymail's Amazon SES inbound webhook recognizes `receipt.action.type == "S3"` and fetches the raw MIME from S3 via boto3 (using the existing Fargate task role).

```python
# ocs_deploy/ses_inbound.py — _create_receipt_rules
actions=[
    ses_actions.S3(
        bucket=self.bucket,
        object_key_prefix=INBOUND_PREFIX,
        topic=self.topic,
    ),
]
```

The `aws_ses_actions.Sns` and `aws_ses_actions.EmailEncoding` imports become unused and are removed.

## Data Flow

Only the SES → SNS leg changes; everything else is unchanged.

| Step | Before | After |
|---|---|---|
| SES writes MIME to `s3://…/inbound/<id>` | yes (S3 action) | yes (S3 action) |
| SES publishes to SNS | full base64-encoded email body inline (150 KB ceiling) | metadata-only `"Received"` notification — `receipt.action.type=S3`, `bucketName`, `objectKey` |
| Lambda forwarder relays SNS payload to Django | unchanged | unchanged (smaller payload, transparent) |
| Django anymail webhook | parses inline `content` | no inline `content`; boto3 GET from S3 via task role |
| `anymail.signals.inbound` fires → Celery task | unchanged | unchanged |

Effective ceiling rises from ~150 KB to the SES inbound max (40 MB). Stated support: 10 MB, matching the 2026-04-28 design.

## Files Changed

| File | Change |
|---|---|
| `ocs_deploy/ses_inbound.py` | Replace the rule's `actions=[S3(...), Sns(encoding=BASE64)]` with `actions=[S3(..., topic=self.topic)]`. Drop the `aws_ses_actions.Sns` and `aws_ses_actions.EmailEncoding` references. SNS topic resource itself is retained — same ARN, same lambda subscription. |
| `ocs_deploy/tests/test_ses_inbound_stack.py` | Update the receipt-rule assertion: exactly one action, `S3Action` with `BucketName`, `ObjectKeyPrefix=inbound/`, and `TopicArn` populated; assert no `SNSAction` element exists on the rule. |

**Not changed:**

- `ocs_deploy/fargate.py` — Django task role already has `s3:GetObject` on `<bucket>/inbound/*` (`fargate.py:493-501`) and `sns:ConfirmSubscription` on the topic.
- `ocs_deploy/lambdas/anymail_forwarder/handler.py` — relays the SNS `Message` field verbatim; payload-shape change is transparent.
- Anymail / Django app settings — anymail's webhook auto-fetches from S3 when `receipt.action.type=S3` and uses boto3's default credential chain (the Fargate task role).
- S3 bucket, bucket policy, lifecycle rule, SNS topic, secret, DNS records — all untouched.

## Why this is a near-revert to the original spec

The 2026-04-28 design's data-flow section says Django fetches from S3. The implementation diverged at one line: `Sns(encoding=ses_actions.EmailEncoding.BASE64)`. With `Encoding` set, SES inlines the full email body in the SNS notification — capped at 150 KB. The fix removes that inline path entirely; metadata reaches Django via SNS, body reaches Django via S3 boto3 fetch.

## Failure Modes

| Mode | Before | After |
|---|---|---|
| Email between 150 KB and 10 MB | bounce ("Message length exceeds limit set by recipient") | delivered |
| Email > 10 MB but ≤ 40 MB | bounce | mechanically delivered; out of stated support scope (document in README if needed) |
| Email > 40 MB | bounce (SES inbound max) | unchanged (still bounces) |
| S3 object missing when anymail fetches | n/a | anymail raises; SNS retries (same retry surface as before) |
| ALB/Django slow on large S3 fetch | n/a | 15-second SNS budget — comfortably fits a 10 MB S3 GET from us-east-1 |
| Lambda forwarder throws | SNS retries | unchanged |

## Testing

### CDK synth test

Update `ocs_deploy/tests/test_ses_inbound_stack.py` to assert:

- The synthesized `AWS::SES::ReceiptRule` has exactly one entry in `Actions`.
- That entry is an `S3Action` with `BucketName` referencing the inbound bucket, `ObjectKeyPrefix=inbound/`, and `TopicArn` referencing the inbound SNS topic.
- No `SNSAction` element appears on the rule.
- `Recipients` list, bucket policy, and lifecycle rule remain as before (regression guard).

### Manual post-deploy validation

1. Re-send the original 500 KB PDF email to `chat.openchatstudio.com`. Expect: no bounce; `anymail.signals.inbound` fires; the attachment is present.
2. `aws s3 ls s3://ocs-<env>-ses-inbound-mail/inbound/` confirms the object was written.
3. Lambda CloudWatch log shows successful POST to the anymail webhook (HTTP 200).
4. Django CloudWatch log shows the inbound signal handler running with the attachment metadata.
5. Send a ~9 MB attachment as upper-bound smoke test; expect success.

## Deploy / Cutover

```
ocs --env <env> aws.deploy --stacks ses-inbound
```

CFN updates the rule's action set in place. The SNS topic, S3 bucket, secret, bucket policy, and lifecycle rule are unchanged. After deploy, run `aws ses describe-active-receipt-rule-set` to confirm the rule set is still active (the rule's actions change but the rule set itself is not recreated; activation should persist).

No application deploy required. No DNS or secret rotation. No data migration. Roll-forward only — if anything regresses, the previous CDK commit can be redeployed.

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Support ceiling | 10 MB, matching the 2026-04-28 design. |
| 2 | How to keep the SNS notification path | Use `S3Action`'s built-in optional `topic` parameter — single action, metadata-only notification. |
| 3 | Lambda forwarder fate | Unchanged. Pass-through behavior already works for the smaller payload. |
| 4 | Where the body fetch happens | Django anymail webhook, via boto3 + existing task role permission. |
| 5 | Replace SNS with S3 ObjectCreated event? | No. Bigger change, no benefit at the chosen support ceiling. |

## Open Questions

None.
