# Deploying the hosted app

How to run `spec2test deploy serve` on Fly.io for design partners. The `Dockerfile` is not
tied to Fly: any host that runs a container with a persistent volume at `/data` works, with
the same environment variables. Values this guide cannot know are in [brackets].

## What lives where

| Where | What |
| --- | --- |
| The image | Code only (`.dockerignore` keeps documents, gold sets, review logs and `.env` out). |
| The volume, `/data` | `deploy.toml`, `workspaces/<id>/review.sqlite`, `usage.sqlite`, Docling's model cache. |
| Fly secrets | The model API key and the five `SPEC2TEST_AUTH_*` sign-in settings. |

## 1. A sign-in provider

Create an OpenID Connect client with any provider; for Google, a "Web application" OAuth
client in the Google Cloud console. Its redirect URI is the app's address plus
`/oauth2callback`, e.g. `https://[app].fly.dev/oauth2callback`.

| Variable | Value |
| --- | --- |
| `SPEC2TEST_AUTH_CLIENT_ID` | the client ID |
| `SPEC2TEST_AUTH_CLIENT_SECRET` | the client secret |
| `SPEC2TEST_AUTH_METADATA_URL` | the provider's discovery URL; Google: `https://accounts.google.com/.well-known/openid-configuration` |
| `SPEC2TEST_AUTH_REDIRECT_URI` | `https://[app].fly.dev/oauth2callback` |
| `SPEC2TEST_AUTH_COOKIE_SECRET` | a random string of 32 or more characters, e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"` |

## 2. The app and its volume

```bash
fly launch --no-deploy --copy-config --name [app]   # keeps fly.toml; set app = "[app]" in it
fly volumes create spec2test_data --region ord --size 3
fly secrets set KIMI_CODE_API_KEY=[key] \
  SPEC2TEST_AUTH_CLIENT_ID=[id] SPEC2TEST_AUTH_CLIENT_SECRET=[secret] \
  SPEC2TEST_AUTH_METADATA_URL=[url] SPEC2TEST_AUTH_REDIRECT_URI=https://[app].fly.dev/oauth2callback \
  SPEC2TEST_AUTH_COOKIE_SECRET=[random]
```

`fly.toml` asks for a 2-CPU, 4 GB machine (Docling's PDF models need a few GB) that stops
when idle and starts on the next visit. The first PDF after a fresh volume downloads
Docling's models (about 500 MB) into `/data/cache`.

## 3. The deployment config

Copy `examples/deploy/deploy.toml`, fill in the workspaces, the cap and the
`[data_page]` facts (host, contact, the provider's data terms, how long volume snapshots
are kept), and check it locally:

```bash
uv run spec2test deploy check deploy.toml
```

Then deploy, and put the config on the volume:

```bash
fly deploy
fly ssh sftp shell        # put deploy.toml /data/deploy.toml
fly machine restart
```

The app reads the config at every page load, so adding a member is: edit the file on the
volume (`fly ssh console`, then edit `/data/deploy.toml`). Run `spec2test deploy check`
there after each change; the app shows an error page while the config is invalid.

## 4. Before the first partner signs in

- Sign in yourself with an email you listed; upload the invented widget lamp spec
  (`uv run spec2test ingest` on a test document, or a small spec of your own), run it,
  accept an item, open *Your data*, delete.
- Sign in with an email that no workspace lists: it must see only the data-handling page.
- Read the data-handling page as the partner will: no [brackets] left.
- Check the model provider's terms allow sending a partner's documents through your API
  key. Kimi: allowed, per the operator (Oct 31, 2026).

## Running it

- **Metrics**: `fly ssh console -C "/app/.venv/bin/spec2test deploy metrics /data/deploy.toml"`.
- **A deletion request by email**: `fly ssh console -C "/app/.venv/bin/spec2test deploy delete /data/deploy.toml [id] --yes"`,
  then reply when it is done. Snapshots of the volume still hold the data until they expire.
- **The budget is used up**: runs refuse to start until the next UTC month. Raise the cap in
  `/data/deploy.toml` to allow more.
- **Removing a partner**: delete their workspace's data, then their table in the config.
