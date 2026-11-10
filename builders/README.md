# Example workflows from Lead Gen Jay

These are reference examples of real workflows from Lead Gen Jay's account, shared with his
permission. They're here so you can see how a multi-step workflow gets put together: email nurture
sequences, waits, tags, an SMS step, and one sequence split into separate tag-triggered branches.

Read them for ideas. They're not a one-click install:

- Each one reads its email copy from files that aren't included here, so they won't run as-is.
- Some take `--update <workflow_id>` and overwrite an existing workflow. Never run one without
  reading it first.
- The copy, offers and links in them are Jay's business, not yours.

| File | What it builds |
|---|---|
| `wf1-course-interest-builder.py` | A 10-email course interest nurture over 14 days |
| `wf5-ht-interest-builder.py` | A high-ticket nurture: 5 emails and 1 SMS over 8 days |
| `wf6-post-call-sales-builder.py` | Post-call sales follow-up, as three tag-triggered branch workflows |
| `consulti-nurture-builder.py` | An 8-email free-trial nurture over 14 days |
| `post-purchase-nurture-builder.py` | A 6-email post-purchase nurture over 35 days |
| `email-sequences-doc-builder.py` | Gathers the sequences into one review document (doesn't touch GHL) |
| `_email_sequences_parser.py` | Shared helper that turns the email markdown into GHL email HTML |

`docs/workflows-map.md` lists Jay's whole account of workflows by name, which is great for ideas
on what to automate.

## Building your own

Read one for ideas, then write a spec and use the CLI. `preview` shows the step tree and changes
nothing. `build` creates the workflow as a draft in your own sub-account:

```bash
~/ghl-cli/.venv/bin/ghl --experimental workflows preview --from-spec my-workflow.json
~/ghl-cli/.venv/bin/ghl --experimental workflows build --from-spec my-workflow.json
```

On Windows (PowerShell):

```powershell
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" --experimental workflows preview --from-spec my-workflow.json
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" --experimental workflows build --from-spec my-workflow.json
```

Easier still: tell Claude what you want ("build me a draft workflow that tags new leads, waits a
day, then sends a welcome email"). It writes the spec, shows you the preview, and builds it after
your yes. The spec format is in the `ghl-cli` skill that setup installed.
