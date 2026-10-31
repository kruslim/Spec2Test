# How your data is handled

This page says what the hosted Spec2Test keeps, who can see it, and how to delete it.

## What we keep

- **The documents you upload**, after they are read into text (sections, headings and
  page numbers). The original file is read in a temporary folder and is not kept.
- **What the pipeline makes from them**: the requirements and the verification items.
- **Your review decisions**: accept, edit or reject, your edits and notes, the email you
  signed in with, and the time.
- **A usage ledger**: for each model call, the number of tokens, the model, your
  workspace's name and the time. It holds no text from your documents.

## Who can see it

- Your data sits in your workspace's own folder. Only the people invited to your
  workspace can sign in to it. Other workspaces cannot open it.
- The person who runs this service can read the server's files, and does so only to keep
  the service running or when you ask for help.
- We do not sell your data or show it to other customers.

## Where it goes

- The app runs on {host}. Your data is stored there.
- When you run the pipeline, the text of your document is sent to the model provider
  that runs the model: **{provider}** (model `{model}`). Their terms decide how long they
  keep it and what they may use it for: {provider_terms}.
- Signing in goes through your sign-in provider, which tells the app your email.
- Nothing else leaves the server. The app sends no usage statistics.

## How long we keep it

Until you delete it, or until the pilot ends, whichever comes first. When the pilot ends
we delete every workspace and tell you first.

## Deleting your data

Open **Your data** and use **Delete this workspace's data**. It removes, at once and for
every member of your workspace, every document, generated item and review decision.
It cannot be undone. Copies in the server's backups are gone within {backup_days} days.

The usage ledger keeps its rows (token counts, model, time and workspace name; no
document text), because it is how the monthly cost limit is kept.

## Questions

Write to {contact}.
