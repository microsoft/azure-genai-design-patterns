---
name: account-review
description: Produce a structured review of one customer account (health, pipeline, recent orders, policy considerations, and recommended next steps) using Contoso CRM, knowledge, and catalog tools. Use when the user asks for an account review, account plan, QBR prep, or "how is <account> doing and what should I do next". Do not use for a quick single-fact lookup.
---

# Account review

<!--
Packaging: copy this file to skills/account-review/SKILL.md in the app package.
The declarative agent references the folder with:
  "agent_skills": [{ "folder": "skills/account-review" }]
Only the front matter (name, description) is loaded up front. The body below is
loaded when the orchestrator decides this skill applies, so it does not count
against the 8,000-character instruction budget.
-->

## Purpose

Give an account manager a review they can act on in five minutes: where the account stands, what changed, what is at risk, and the next three actions. Every number comes from a tool call in this conversation.

## Inputs

| Input | Required | How to get it |
|---|---|---|
| Account ID (`ACC-####`) | Yes | If the user gives a name, ask for or confirm the ID. Do not guess between similar names. |
| Review horizon | No | Default to the current quarter. |
| Focus area | No | For example renewal, expansion, or risk. Default to all three. |

## Workflow

Follow the steps in order. Stop and ask the user when a step says so.

### Step 1: Account snapshot

1. Call `servicecrm.get_account_summary` with the account ID.
2. If the result has `isError: true`, explain the reason and stop.
3. Record health score, pipeline value, open opportunity count, and recent orders.
4. If an interactive dashboard view is returned, still write the one-line snapshot in text.

### Step 2: Pipeline

1. Call `servicecrm.search_opportunities` with `owner` set to `anyone` and `close_before` set to the end of the review horizon.
2. Keep only rows for this account. If `next_cursor` is present and fewer than five rows match, call once more with the cursor. Do not page further.
3. Flag any opportunity in `negotiation` that closes within 14 days.

### Step 3: Policy and contract context (only if relevant)

Call a `serviceknowledge` search tool only when Step 1 or Step 2 shows one of these:

- An order or opportunity above 250,000 USD (approval policy).
- A health score below 60 (escalation playbook).
- A renewal in the horizon (renewal terms).

Cite the document title and link for each policy you use. Skip this step otherwise.

### Step 4: Product check (only if recommending an order)

If a next step would be an order, call `servicecatalog` to confirm the SKU and list price. Never quote a discount; hand pricing exceptions to the Pricing Specialist.

### Step 5: Write the review

Use exactly this structure:

```markdown
**<Account name> (<ACC-####>) — review for <horizon>**

**Snapshot:** health <score>/100, pipeline <value> <currency> across <n> opportunities, last order <date>.

| Area | Status | Evidence |
|---|---|---|
| Health | <Green/Amber/Red> | <one fact> |
| Pipeline | <Green/Amber/Red> | <one fact> |
| Orders | <Green/Amber/Red> | <one fact> |

**Risks**
- <risk with the fact that supports it>

**Recommended next steps**
1. <action, owner, date>
2. <action, owner, date>
3. <action, owner, date>

**Sources:** <tools and documents used>
```

Status rules: Green when health is 75 or more; Amber from 60 to 74; Red below 60, or when any negotiation-stage opportunity closing within 14 days has no activity noted.

### Step 6: Offer follow-ups (do not perform them)

Offer, but do not perform without an explicit request:

- "Save this account to your shortlist" (calls `servicecrm.save_shortlist`; the user sees a confirmation card).
- "Draft an order for the recommended SKU" (calls `servicecrm.draft_order`; creates a draft only).
- "Notify the account team" (requires an approval token; explain how to get one if the user has none).

## Guardrails

- Read-only by default. This skill never calls `submit_order` or `notify_account_team`.
- Do not include personal data beyond business contact names already returned by the tools.
- If any tool fails, finish the review with what you have and mark the missing area as "Not available" with the reason.
- Keep the review under 300 words, excluding the table.

## Example

User: "Give me an account review for Fabrikam Retail."

1. Confirm the ID: "Fabrikam Retail is ACC-1001. Reviewing for the current quarter."
2. `get_account_summary` returns health 82, pipeline 410,000 USD, three opportunities, last order ORD-5531 on 2026-10-14.
3. `search_opportunities` returns two rows for ACC-1001; one is in negotiation and closes in nine days.
4. No order above 250,000 USD and health is above 60, so Step 3 is skipped.
5. The review shows Health Green, Pipeline Amber (negotiation closing soon), Orders Green, and three next steps.
