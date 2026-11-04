# Remote server (claude.ai and the Claude mobile apps)

Desktop AI apps start ricelo on your computer (see the [README](../README.md)); that is the private default and needs nothing here. claude.ai in a browser and the Claude phone apps can only reach an MCP server **by URL**, so to use ricelo from them you run your own copy on a server.

This is for **one person**: the server holds *your* Garmin key and only you can connect, by typing a password you choose. It runs on Google Cloud Run, which for one person's use fits in the free tier (Google still requires a billing account).

```
Claude (web / phone) ──HTTPS──► https://mcp.ricelo.com/mcp ──► Cloud Run: ricelo ──► Garmin
                                 (OAuth: you approve once with your password)
```

## How access is protected

- The server is its own OAuth sign-in. The first time you add it, Claude opens a ricelo page that asks for your **owner password**; after that Claude holds a token (1 hour, renewed automatically for up to 30 days of no use).
- Only sign-in redirects to `claude.ai`, `claude.com` and `localhost` are accepted, so another website can't register itself and trick you into approving it. Change this with `RICELO_REDIRECT_HOSTS`.
- Tokens are signed with `RICELO_AUTH_SECRET` and nothing is stored, so the server survives restarts. **To sign every app out, change `RICELO_AUTH_SECRET`.**
- 10 wrong passwords within 15 minutes lock sign-in for that instance.
- The server refuses to start on a public address without these settings.

## Settings

| Variable | Value |
| :--- | :--- |
| `GARMIN_PERSONAL_API_KEY` | Your Garmin Personal API key (store as a secret) |
| `RICELO_OWNER_PASSWORD` | A password of 12+ characters that you type when connecting (secret) |
| `RICELO_AUTH_SECRET` | Random string of 32+ characters, e.g. `openssl rand -hex 32` (secret) |
| `RICELO_PUBLIC_URL` | The URL people reach, without `/mcp`, e.g. `https://mcp.ricelo.com` |
| `RICELO_REDIRECT_HOSTS` | Optional; comma-separated hosts allowed as sign-in redirects |

The [Dockerfile](../Dockerfile) sets `RICELO_TRANSPORT=http`, `RICELO_HOST=0.0.0.0` and `PORT=8080`.

## Deploy to Cloud Run

Needs the [gcloud CLI](https://cloud.google.com/sdk/docs/install) and a Google Cloud project with billing enabled.

1. Store the secrets (each command prompts for the value; nothing lands in your shell history):
   ```bash
   gcloud services enable run.googleapis.com secretmanager.googleapis.com cloudbuild.googleapis.com
   for s in garmin-key ricelo-owner-password ricelo-auth-secret; do
     printf "%s: " "$s" && read -s v && echo && printf "%s" "$v" | gcloud secrets create "$s" --data-file=-
   done
   ```
   Give the Cloud Run service account access: `gcloud projects add-iam-policy-binding PROJECT_ID --member=serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com --role=roles/secretmanager.secretAccessor`.

2. Deploy from the repo root:
   ```bash
   gcloud run deploy ricelo --source . --region us-central1 \
     --allow-unauthenticated --min-instances 0 --max-instances 1 \
     --set-env-vars RICELO_PUBLIC_URL=https://mcp.ricelo.com \
     --set-secrets GARMIN_PERSONAL_API_KEY=garmin-key:latest,RICELO_OWNER_PASSWORD=ricelo-owner-password:latest,RICELO_AUTH_SECRET=ricelo-auth-secret:latest
   ```
   `--allow-unauthenticated` lets Claude reach the URL; ricelo's own OAuth then decides who gets in. `--max-instances 1` caps cost and keeps the failed-password limit in one place.

   To try it before setting up a domain, deploy with `RICELO_PUBLIC_URL` set to the `https://ricelo-….run.app` URL the first deploy prints, then deploy again.

3. Point your domain at it (here `mcp.ricelo.com`, DNS on Cloudflare):
   ```bash
   gcloud beta run domain-mappings create --service ricelo --domain mcp.ricelo.com --region us-central1
   ```
   Verify the domain in Google Search Console if asked, then in Cloudflare DNS add `CNAME mcp → ghs.googlehosted.com` with the **proxy off (grey cloud)**. The HTTPS certificate is ready after 15–60 minutes.

4. Set a budget alert (Billing → Budgets & alerts, e.g. $1) so any cost is noticed.

## Connect Claude

1. On claude.ai: **Settings → Connectors → Add custom connector**, URL `https://mcp.ricelo.com/mcp`.
2. Click **Connect**; the ricelo page opens. Check it names `claude.ai`, enter your owner password and press **Allow**.
3. The connector is then available in the Claude phone apps too.

Claude Code works as well: `claude mcp add --transport http ricelo https://mcp.ricelo.com/mcp`.

## Update or remove

- **Update** to new code: run the deploy command again.
- **Sign everything out:** add a new version of `ricelo-auth-secret` and redeploy.
- **Remove:** `gcloud run services delete ricelo --region us-central1`, delete the domain mapping and secrets, and revoke the Garmin key in Garmin Connect if you no longer use it.

## Run it locally over HTTP

For testing, without authentication (only allowed on loopback):

```bash
RICELO_TRANSPORT=http uv run ricelo-running-science-mcp   # http://127.0.0.1:8000/mcp
```
