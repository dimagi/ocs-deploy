# SES Inbound Size-Limit Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise the inbound-email size ceiling from ~150 KB back to the original 10 MB target by removing the inline-SNS receipt action and routing the email body fetch to anymail's built-in S3 path.

**Architecture:** In `ocs_deploy/ses_inbound.py`, replace the receipt rule's two actions (`S3` + `Sns(encoding=BASE64)`) with a single `S3` action whose optional `topic` parameter publishes a metadata-only "Received" notification to the existing SNS topic. Anymail's webhook handler auto-fetches the raw MIME from S3 via boto3 using the Fargate task role's existing `s3:GetObject` permission. SNS topic, lambda forwarder, secret, bucket, bucket policy, and lifecycle rule are unchanged.

**Tech Stack:** Python 3.13, AWS CDK (`aws-cdk-lib==2.251.0`), `aws_cdk.aws_ses_actions`, pytest, `aws_cdk.assertions.Template`, uv.

**Spec:** [`docs/superpowers/specs/2026-04-30-ses-inbound-size-fix-design.md`](../specs/2026-04-30-ses-inbound-size-fix-design.md)

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `ocs_deploy/ses_inbound.py` | Modify | CDK stack defining the SES inbound rule. Receives the action-set change. |
| `ocs_deploy/tests/test_ses_inbound_stack.py` | Modify | Synth tests. Replace the obsolete `test_receipt_rule_actions_are_s3_then_sns` test with assertions for the new single-action shape. |

No other files change. The Fargate stack already grants `s3:GetObject` on `<bucket>/inbound/*` (`fargate.py:493-501`); the lambda forwarder passes the SNS message through verbatim; the SNS topic ARN, secret, and bucket are reused.

---

## Task 1: Update the synth test to express the new contract

We're switching the rule from two actions (`S3` + `SNS`) to a single `S3` action with an embedded SNS topic. The current test `test_receipt_rule_actions_are_s3_then_sns` asserts the old shape and must be replaced. Following TDD, we update the test first, watch it fail against the unchanged production code, then make the production change to flip it green.

**Files:**
- Modify: `ocs_deploy/tests/test_ses_inbound_stack.py:122-148`

- [ ] **Step 1: Replace the obsolete `test_receipt_rule_actions_are_s3_then_sns` test**

Open `ocs_deploy/tests/test_ses_inbound_stack.py`. Replace the block from line 122 through line 148 (the entire `test_receipt_rule_actions_are_s3_then_sns` function and its trailing blank line) with the two new tests below. Leave every other test in the file untouched.

```python
def test_receipt_rule_has_single_s3_action_with_sns_topic(ocs_config):
    """Rule has exactly one action — an S3 action that also notifies SNS.

    A separate SNSAction would force SES to inline the email body in the SNS
    notification, which caps inbound mail at 150 KB. Using S3Action.topic
    publishes only a metadata "Received" notification; anymail fetches the
    body from S3 via the Fargate task role.
    """
    template = _synth(ocs_config)
    template.has_resource_properties(
        "AWS::SES::ReceiptRule",
        {
            "Rule": assertions.Match.object_like(
                {
                    "Actions": [
                        assertions.Match.object_like(
                            {
                                "S3Action": assertions.Match.object_like(
                                    {
                                        "ObjectKeyPrefix": "inbound/",
                                        "TopicArn": assertions.Match.any_value(),
                                    }
                                )
                            }
                        ),
                    ],
                }
            ),
        },
    )


def test_receipt_rule_has_no_separate_sns_action(ocs_config):
    """Guard against re-introducing the inline-body SNSAction."""
    template = _synth(ocs_config)
    rules = template.find_resources("AWS::SES::ReceiptRule")
    assert len(rules) == 1
    actions = next(iter(rules.values()))["Properties"]["Rule"]["Actions"]
    for action in actions:
        assert "SNSAction" not in action, (
            f"SNSAction must not appear on the receipt rule (found in {action!r}); "
            "the SNS notification path is now driven by S3Action.topic."
        )
```

- [ ] **Step 2: Run the new tests and confirm they fail**

Run:

```bash
uv run pytest ocs_deploy/tests/test_ses_inbound_stack.py::test_receipt_rule_has_single_s3_action_with_sns_topic ocs_deploy/tests/test_ses_inbound_stack.py::test_receipt_rule_has_no_separate_sns_action -v
```

Expected: both tests **FAIL**. The first fails because the rule currently has *two* actions, not one. The second fails because the second action is an `SNSAction`. If either test passes here, the test isn't actually exercising the new contract — re-check the test code.

- [ ] **Step 3: Commit the failing test**

```bash
git add ocs_deploy/tests/test_ses_inbound_stack.py
git commit -m "test(ses-inbound): assert single S3 action with SNS topic"
```

---

## Task 2: Switch the receipt rule to a single S3 action with SNS topic

Now make the production change that flips the new tests green.

**Files:**
- Modify: `ocs_deploy/ses_inbound.py:106-127` (the `_create_receipt_rules` method body) and the `aws_ses_actions` import block at lines 4-13.

- [ ] **Step 1: Update `_create_receipt_rules` to use a single S3 action**

Open `ocs_deploy/ses_inbound.py`. Find the `actions=[…]` argument inside the `rule_set.add_rule(...)` call (currently lines 117-126). Replace the entire `actions=[…]` block with the version below. The `recipients`, `scan_enabled`, and `enabled` arguments stay as they are.

Before:

```python
            actions=[
                ses_actions.S3(
                    bucket=self.bucket,
                    object_key_prefix=INBOUND_PREFIX,
                ),
                ses_actions.Sns(
                    topic=self.topic,
                    encoding=ses_actions.EmailEncoding.BASE64,
                ),
            ],
```

After:

```python
            actions=[
                ses_actions.S3(
                    bucket=self.bucket,
                    object_key_prefix=INBOUND_PREFIX,
                    topic=self.topic,
                ),
            ],
```

- [ ] **Step 2: Run the two new tests and confirm they pass**

Run:

```bash
uv run pytest ocs_deploy/tests/test_ses_inbound_stack.py::test_receipt_rule_has_single_s3_action_with_sns_topic ocs_deploy/tests/test_ses_inbound_stack.py::test_receipt_rule_has_no_separate_sns_action -v
```

Expected: both tests **PASS**.

- [ ] **Step 3: Run the full `test_ses_inbound_stack.py` file as a regression guard**

Run:

```bash
uv run pytest ocs_deploy/tests/test_ses_inbound_stack.py -v
```

Expected: every test in the file **PASSES**. In particular, `test_sns_topic_created`, `test_sns_subscription_targets_forwarder_lambda`, `test_forwarder_lambda_has_webhook_url_and_secret_env`, and `test_forwarder_lambda_can_read_webhook_secret` must still pass — they verify the SNS topic, lambda subscription, secret, and lambda env are unchanged.

If any of these regress, stop. The change should not affect them; investigate before continuing.

- [ ] **Step 4: Run the full repo test suite to catch cross-stack regressions**

Run:

```bash
uv run pytest -q
```

Expected: every test **PASSES**. This catches anything in `test_app_synth.py`, `test_fargate_stack.py`, etc. that depends on the receipt-rule shape or its outputs.

If any test fails outside `test_ses_inbound_stack.py`, stop and investigate — there should be no functional dependency on the inline SNS encoding.

- [ ] **Step 5: Run the linter**

Run:

```bash
uv run ruff check ocs_deploy/
```

Expected: no errors. (The pre-commit hook will also run this.)

- [ ] **Step 6: Synth the stack to confirm CDK accepts the new shape**

Run:

```bash
uv run cdk synth --quiet ocs-test-ses-inbound 2>&1 | tail -5
```

Expected: synth succeeds (last lines show the stack name or no output, and a zero exit). If the stack name in your `.env` differs, substitute the appropriate `<app>-<env>-ses-inbound`. This is a belt-and-braces check on top of the assertions tests.

- [ ] **Step 7: Commit the production change**

```bash
git add ocs_deploy/ses_inbound.py
git commit -m "$(cat <<'EOF'
fix(ses-inbound): use S3Action.topic to lift 150 KB inbound size cap

SES caps the inline SNSAction email payload at 150 KB, which bounced
inbound emails larger than ~150 KB with "Message length exceeds limit
set by recipient." Replace the dual S3 + SNSAction(encoding=BASE64)
rule with a single S3Action that uses its optional topic= parameter
to publish a metadata-only Received notification.

Anymail's Amazon SES inbound webhook recognizes receipt.action.type
== "S3" and fetches the raw MIME from S3 via boto3 using the Fargate
task role's existing s3:GetObject permission. Effective ceiling rises
to the SES inbound max (40 MB); stated support remains 10 MB per the
2026-04-28 inbound-email design.

Spec: docs/superpowers/specs/2026-04-30-ses-inbound-size-fix-design.md
EOF
)"
```

---

## Task 3: Manual post-deploy validation runbook

CDK changes can't be verified in CI alone — the actual SES → S3 → SNS → Lambda → Django path needs a real email to confirm. This task documents the steps; an operator runs them after `aws.deploy --stacks ses-inbound`.

**Files:**
- None (operator-executed).

- [ ] **Step 1: Deploy the stack**

```bash
ocs --env <env> aws.deploy --stacks ses-inbound
```

Expected: CFN updates the receipt rule's `Actions` in place. No churn on `AWS::SNS::Topic`, `AWS::S3::Bucket`, `AWS::SecretsManager::Secret`, or `AWS::SNS::Subscription` resources (CDK should report these as unchanged).

- [ ] **Step 2: Confirm the active receipt rule set is unchanged**

```bash
aws ses describe-active-receipt-rule-set --query 'Metadata.Name' --output text
```

Expected: prints `<app>-<env>-inbound` (the same rule-set name you set active during the original deploy). If this prints `None` or a different name, run the activation command from the stack's `ActivateReceiptRuleSetCommand` output before continuing.

- [ ] **Step 3: Reproduce the original failure case**

Send the original 500 KB PDF email to `chat.openchatstudio.com` (or whichever inbound address triggered the bounce). Use whatever email client surfaced the original report.

Expected: no bounce returned to the sender's inbox.

- [ ] **Step 4: Confirm S3 received the raw MIME**

```bash
aws s3 ls s3://ocs-<env>-ses-inbound-mail/inbound/ | tail -3
```

Expected: a recent object (size ≈ original message size). The bucket name comes from the `SesInboundBucketName` stack output.

- [ ] **Step 5: Confirm the Lambda forwarder POSTed to the anymail webhook**

In CloudWatch Logs, open the `/aws/lambda/<app>-<env>-AnymailForwarderFn-...` log group and inspect the most recent invocation. Expected: no errors; the `urllib.request.urlopen` call returns 200.

- [ ] **Step 6: Confirm Django fetched from S3 and fired the inbound signal**

In the Django app's CloudWatch log group, look for the most recent `anymail.signals.inbound` handler entry. Expected: handler ran with the attachment metadata present (filename, content type, size). If anymail's S3 fetch failed, the log will show a boto3 `AccessDenied` or `NoSuchKey` error — investigate the task role and bucket key.

- [ ] **Step 7: Upper-bound smoke test**

Send an email with a ~9 MB attachment to the same inbound address.

Expected: same successful path. The 15-second SNS retry budget comfortably accommodates a 10 MB S3 GET from us-east-1; if Django logs show an SNS-side timeout, capture the timing and re-evaluate the support ceiling.

---

## Self-Review (completed)

**Spec coverage:**
- "Replace the rule's `actions=[S3(...), Sns(encoding=BASE64)]` with `actions=[S3(..., topic=self.topic)]`" → Task 2, Step 1.
- "Drop the `aws_ses_actions.Sns` and `aws_ses_actions.EmailEncoding` references" → Task 2, Step 1 (the references disappear when the lines are removed; the `import aws_ses_actions as ses_actions` alias stays because `ses_actions.S3` is still used).
- "Update `ocs_deploy/tests/test_ses_inbound_stack.py`" → Task 1.
- Manual post-deploy validation steps → Task 3.
- Failure modes / decision table → captured in spec; no plan task needed (CDK behavior, not new code).

**Placeholder scan:** No "TBD", "TODO", "implement later", or "add appropriate error handling". Every code change has the literal before/after code shown.

**Type consistency:** `topic=self.topic` matches the existing attribute on `SesInboundStack` (assigned at `ses_inbound.py:31`). `INBOUND_PREFIX` is unchanged. `ses_actions.S3` is the same class already imported and used.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-30-ses-inbound-size-fix.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task with two-stage review between tasks.
2. **Inline Execution** — execute tasks in this session using executing-plans with checkpoints.

Which approach?
