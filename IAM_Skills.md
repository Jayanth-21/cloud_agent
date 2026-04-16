# IAM Onboarding & Offboarding Skills — Research & Implementation Plan

This document answers: **Is it possible?** **What would need to change?** and **What are the risks and ordering concerns?** for adding two new agent capabilities:

1. **Onboarding** — two fixed personas: **Data Scientist** (S3, Bedrock, DynamoDB, SageMaker) and **Developer** (S3, Lambda, EventBridge).
2. **Offboarding** — remove the principal from **all** IAM groups and **remove their access** to the AWS account.

No code has been implemented; this is research and a proposed roadmap aligned with the current repo (`lambdas/unified_tools/`, Gateway inline schema, `agent/skills/`, LangGraph in `agent/src/graph/`).

---

## 1. Current baseline (what exists today)

| Area | Today |
|------|--------|
| **unified_tools Lambda** | Read-only: Cost Explorer, CloudWatch (metrics/alarms/logs), CloudTrail queries, Lambda/ECS/Config **discovery**. **`boto3` clients:** `ce`, `cloudwatch`, `logs`, `cloudtrail`, `lambda`, `ecs`, `config`. **No `iam` client.** |
| **Lambda execution policy** | `lambdas/unified_tools/iam-policy-unified-tools.json` — **no IAM actions**. |
| **Gateway** | Inline tool schema in `lambdas/gateway_inline_schema.json`; tools map to Lambda `handler.py` dispatch. |
| **Agent** | Skills under `agent/skills/*/SKILL.md`; skill text is injected into graph prompts; **all** Gateway tools are available to the graph (no allowlist filter in current design). |
| **LangGraph** | Planner → tool selection → execute → evaluate → loop → prepare_viz → generate_response. **Execute** calls MCP → Lambda. **`ask_user`** exists for clarification. **Cost date guard** is unrelated to IAM. |

**Conclusion:** Onboarding/offboarding **are possible** but require **new write-capable IAM (and possibly Identity Center) paths**, **new IAM permissions for the Lambda role**, **new tools + schema**, **new skills + operational playbooks**, and **strong governance** (human approval, auditing, least privilege). The LangGraph **topology** does not *have* to change; behavior changes mostly live in **tools + prompts + guardrails**.

---

## 2. Identity model — you must choose one (or support both)

Your wording (“user groups”, “access to the AWS account”) maps to different AWS surfaces:

| Model | Onboarding roughly means | Offboarding roughly means |
|-------|---------------------------|-----------------------------|
| **Long-lived IAM user** (console / access keys) | Create user (optional), add to **IAM groups**, attach **managed/custom policies** for S3/Bedrock/etc. | `RemoveUserFromGroup` for every group, delete access keys / login profile / MFA, detach inline policies, **`DeleteUser`** (after dependencies removed). |
| **IAM Identity Center (SSO)** | Assign user to **permission sets** / **groups** in Identity Store; permission sets map to IAM roles in accounts. | Remove assignments from groups / permission sets; may **not** use classic `iam:RemoveUserFromGroup` on an IAM *user* in the account. |
| **Federated only** (no IAM user in account) | Provisioning is outside this Lambda (IdP). Agent might only **document** steps or call **SCIM** APIs elsewhere — not typical in `unified_tools` unless you add integrations. | “Remove access” = IdP deprovision — **different** stack. |

**Recommendation:** Decide explicitly: **IAM users in this account** vs **Identity Center**. The rest of this document assumes **IAM users + IAM groups** in a **single account**, unless you note otherwise — that is the closest match to “remove from all user groups” and “Developer / Data Scientist” policy bundles.

---

## 3. What “access” means for each persona (IAM design work)

The agent should not invent ARNs. You need **stable, reviewed** artifacts in AWS:

**Data Scientist — services: S3, Bedrock, DynamoDB, SageMaker**

- Prefer **IAM groups** per persona, e.g. `cloud-intel-datascientist-base`, with **managed or customer-managed policies** that grant least-privilege actions on **tagged** or **named** resources (or account-wide read/write if policy dictates — higher risk).
- Alternatively **permission boundaries** on the user to cap blast radius.
- Bedrock/SageMaker often need **multiple actions** (InvokeModel, CreateTrainingJob, etc.); scope by resource or prefix.

**Developer — services: S3, Lambda, EventBridge**

- Group e.g. `cloud-intel-developer-base` with policies for `s3:*` (scoped), `lambda:*` (scoped), `events:*` (scoped) as appropriate.

**Offboarding**

- **List** group memberships: `iam:ListGroupsForUser`.
- **Remove** from each: `iam:RemoveUserFromGroup`.
- **Detach** managed policies: `iam:DetachUserPolicy` / `iam:ListAttachedUserPolicies`.
- **Delete** inline user policies: `iam:DeleteUserPolicy` / `iam:ListUserPolicies`.
- **Access keys:** `iam:ListAccessKeys`, `iam:DeleteAccessKey`.
- **Login profile:** `iam:DeleteLoginProfile` (if exists).
- **MFA:** `iam:DeactivateMFADevice` / `iam:ListMFADevices` (device deletion order per AWS docs).
- **SSH public keys / signing certs** if used: list/delete.
- Finally **`iam:DeleteUser`** only when no attachments remain.

**“Shouldn’t have access anymore”** — For IAM users, **`DeleteUser`** after cleanup is the hard cut. If the person uses **assumed roles** only, offboarding is **role session** expiry + **removing role trust** — different tools (`iam:UpdateAssumeRolePolicy`, STS not in classic IAM user flow).

---

## 4. Additional components — checklist

### 4.1 Lambda (`lambdas/unified_tools/`)

| Need | Detail |
|------|--------|
| **New `boto3` client** | `iam = boto3.client("iam")` (and optionally `identitystore` / `sso-admin` if you support Identity Center). |
| **New tool handlers** | Dispatch in `handler.py` (same pattern as existing tools): parse `bedrockAgentCoreToolName`, route to functions that call IAM APIs with **validated inputs** (username, group names from allowlist, persona enum). |
| **Input validation** | Reject unknown groups; enforce **allowlisted** IAM user name pattern (e.g. corporate prefix); optional **dry-run** tool that only returns a diff plan without applying. |
| **Idempotency** | Onboarding: “user already exists” → add to groups / attach missing policies only. Offboarding: “already removed” → no-op or partial success object. |
| **Error shape** | Match existing JSON error pattern so the agent can `evaluate` / retry. |

**Optional split:** A **second Lambda** (e.g. `unified_tools_iam_write`) with a **stricter role** and a **separate Gateway target** so read FinOps traffic never shares an execution role with IAM writes. Tradeoff: two targets, two schemas (or merged schema with two Lambda ARNs). Same repo can still hold both handlers.

### 4.2 Lambda execution role (`iam-policy-unified-tools.json` or a new policy)

You must add **explicit IAM API allows** (and often **resource-level** constraints):

Examples (names illustrative — tighten with your ARNs / paths):

- `iam:CreateUser`, `iam:GetUser`, `iam:DeleteUser`
- `iam:AddUserToGroup`, `iam:RemoveUserFromGroup`, `iam:ListGroupsForUser`
- `iam:AttachUserPolicy`, `iam:DetachUserPolicy`, `iam:ListAttachedUserPolicies`
- `iam:PutUserPolicy`, `iam:DeleteUserPolicy`, `iam:ListUserPolicies`
- `iam:CreateLoginProfile`, `iam:DeleteLoginProfile`, `iam:UpdateLoginProfile` (if console passwords)
- `iam:CreateAccessKey`, `iam:DeleteAccessKey`, `iam:ListAccessKeys` (if keys — often you **disable** keys instead of creating new ones in onboarding flows)
- MFA / signing certificate APIs if applicable

**Least privilege:** Prefer **condition keys** (e.g. `aws:ResourceTag`, `iam:PolicyArn` for specific managed policies you attach) and **deny** `iam:*` on `root` and on `*` resources where possible.

**Separation of duties:** The role that can **delete users** should be rare; consider requiring **two-step** flow (see §6).

### 4.3 Gateway (`lambdas/gateway_inline_schema.json`)

- Add **new tool definitions** (name, description, `inputSchema`) for each IAM operation **or** for **composite** tools (e.g. `onboard_persona` with `{ "username", "persona": "datascientist|developer" }` that Lambda implements as a **transaction-like** sequence with rollback notes).
- **Sync** the Gateway target after deploy (same process as today’s README).

**Composite vs atomic tools:** Fewer, higher-level tools reduce agent mistakes but make Lambda logic heavier and errors harder to map. Atomic tools (add to group, attach policy) are easier to audit in CloudTrail per call.

### 4.4 Agent skills (`agent/skills/`)

Add two packages (folder + `SKILL.md` each):

| Skill | Purpose in `SKILL.md` |
|-------|------------------------|
| **`team-onboarding`** (example id) | When to use; **exact** group names and policy ARNs (placeholders replaced at deploy time); require **`ask_user`** for username + persona confirmation; forbid creating users without approval text; link to runbook. |
| **`team-offboarding`** | When to use; ordered steps (list groups → remove → detach → keys → MFA → delete user); **mandatory** confirmation phrase or ticket id in `ask_user` before `DeleteUser`; never target root or break-glass accounts (name patterns deny list). |

YAML **`tools`** list: enumerate **only** the new Gateway tool base names the skill should reference (documentation for authors; current agent still loads all tools, but playbooks guide the model).

### 4.5 LangGraph (`agent/src/graph/nodes.py` / `build.py`)

| Topic | Need? |
|-------|--------|
| **New node types** | **Not required** for a first version. Existing planner/tool_selection/execute loop can invoke IAM tools. |
| **Guardrails** | **Recommended:** Similar to **cost date guard** — e.g. block `DeleteUser` / `RemoveUserFromGroup` unless `ask_user` returned a confirmation token in the same session, or unless env `IAM_WRITE_ENABLED=true`. That requires **state** or **parsing** tool results — small **execute** or **evaluate** logic change. |
| **Human-in-the-loop** | If you add an **approval API** later, you could merge a `tool_choice` / external approval queue — larger change. |

### 4.6 Runtime / UI (`runtime_invoke.py`, `ui/`)

- **No change** strictly required to *stream* results; final text will describe IAM actions.
- **Charts:** usually **none** for IAM; viz pipeline can ignore.
- **UI suggested prompts** (`ui/lib/constants.ts`): optional fourth/fifth chips for “Onboard …” / “Offboard …” — cosmetic.
- **Audit:** Ensure **CloudTrail** data events or management events capture IAM changes (default for IAM API calls in CloudTrail trail).

### 4.7 Testing & operations

- **Integration tests** against a **sandbox account** with disposable IAM users.
- **Runbook** for partial failure (user removed from 3 of 5 groups).
- **Alerting** on Lambda errors and on **IAM Access Analyzer** / unusual `DeleteUser` volume.

---

## 5. Feasibility summary

| Requirement | Feasible? | Notes |
|-------------|-----------|--------|
| Data Scientist vs Developer **permission bundles** | **Yes** | Implement as **group membership + policies** you pre-create in AWS; agent only attaches known ARNs / group names. |
| Offboard: remove from **all** groups + no access | **Yes** for IAM users | Full cleanup + **`DeleteUser`** is standard; edge cases (resource-based policies referencing user ARN, cross-account roles) need runbook. |
| **Zero** LangGraph changes | **Possible** | Not recommended without **execute-time guards** for destructive APIs. |
| **Identity Center** | **Yes** but **different APIs** | `sso-admin`, `identitystore` — different Lambda permissions and tools. |
| **Fully autonomous** offboarding | **Risky** | Strongly recommend **confirmation** (`ask_user` + user reply in next turn, or external approval). |

---

## 6. Suggested phased rollout

**Phase A — AWS foundations (no agent change)**  
1. Create IAM **groups** and **policies** for Data Scientist and Developer (reviewed by security).  
2. Decide naming: `username` convention.  
3. Extend Lambda IAM role policy (or new Lambda + role) with required **`iam:`** actions; deploy.  
4. Add tools + `handler.py` branches; extend `gateway_inline_schema.json`; sync Gateway.  
5. Manual test each tool from AWS console “test invoke” or CLI.

**Phase B — Agent skills**  
6. Add `SKILL.md` files with strict playbooks and **explicit** group/policy ARNs (or SSM parameters the Lambda resolves — then agent passes **persona** only).  
7. Update `PROJECT_DOCUMENTATION.md` / internal runbooks.

**Phase C — Guardrails (recommended)**  
8. Add **execute-node checks**: e.g. offboarding delete only if prior tool result was `ask_user` with confirmation; or env flag for write enable.  
9. Optional: **read-only dry-run** tool returning planned IAM API list for human approval.

**Phase D — UX**  
10. Optional UI chips; optional **separate chat “mode”** or model routing so IAM writes are not mixed with casual FinOps questions on the same surface without user intent.

---

## 7. Risks and mitigations (short list)

| Risk | Mitigation |
|------|------------|
| Wrong user deleted | Confirmation workflow; deny-list on protected names; **no** wildcards on `DeleteUser`. |
| Over-privileged Lambda role | Separate Lambda + minimal IAM policy; permission boundaries on **created** users. |
| Agent hallucinates group names | Tool input validation against allowlist; SKILL.md lists **exact** strings. |
| Partial offboard | Return structured per-step status; idempotent retries. |
| Compliance / audit | CloudTrail; optional ticket id in tool input; log to S3 from Lambda. |

---

## 8. Reference — IAM API families (illustrative)

**Read (safe for dry-run / planning):** `GetUser`, `ListGroupsForUser`, `ListAttachedUserPolicies`, `ListUserPolicies`, `ListAccessKeys`, `GetLoginProfile`, `SimulatePrincipalPolicy` (if you add simulation).

**Write (onboarding):** `CreateUser`, `AddUserToGroup`, `AttachUserPolicy`, `PutUserPolicy`, `CreateLoginProfile` (if applicable).

**Write (offboarding):** `RemoveUserFromGroup`, `DetachUserPolicy`, `DeleteUserPolicy`, `DeleteAccessKey`, `DeleteLoginProfile`, `DeactivateMFADevice`, `DeleteUser`.

Exact set depends on whether you create users in-chat or only attach to **existing** users.

---

## 9. Deliverables summary (what you asked for)

| Item | Action |
|------|--------|
| **Lambda** | New IAM (and optionally Identity Center) code paths + tests. |
| **Lambda IAM policy** | New / expanded JSON policy; consider second Lambda for blast-radius separation. |
| **Gateway** | New tool entries + sync. |
| **Skills** | Two new `agent/skills/<id>/SKILL.md` packages with playbooks and tool name references. |
| **LangGraph** | Optional but recommended: execute-time **gates** for destructive ops; no mandatory new nodes. |
| **Agent runtime** | No change unless you add gates or new `ask_user` flows in `nodes.py`. |
| **UI** | Optional constants / copy; persistence unchanged. |
| **Docs** | Runbooks, security review, update main project doc when implemented. |

---

*Document: `IAM_Skills.md` (research only; no code changes in repo when this was written).*
