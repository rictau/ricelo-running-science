# Sync Google Health (Fitbit) exercises to Garmin Connect

`ricelo sync-fitbit` copies the exercises your Fitbit or Pixel Watch recorded
into Garmin Connect, so Garmin and ricelo's training-load numbers see your whole
training history.

Neither company offers this sync. ricelo does it in four steps:

1. List your exercise sessions with the **Google Health API**, which replaced
   the Fitbit Web API (the Fitbit Web API was turned off on November 2, 2026).
2. Export each session as a TCX file (`exportExerciseTcx`), which carries the
   GPS route where there is one.
3. Set the TCX sport from the exercise type, add heart rate from your
   heart-rate samples if the export has none, and add a note saying where it came from.
4. Upload the file to Garmin Connect with your Garmin Personal API key, the
   same key the rest of ricelo uses. Garmin documents that key as read-only,
   so this step may be refused; see [Limitations](#limitations) for the
   file-import fallback.

A state file (`~/.config/ricelo/fitbit-sync-state.json`) remembers what was
uploaded, so each run only sends new exercises. A failed upload is not
recorded, so the next run retries it.

## One-time setup

Google doesn't let a script read health data with your password. You create
your own OAuth client, which only you use.

1. In the [Google Cloud console](https://console.cloud.google.com/), create a
   project (or pick one).
2. **APIs & Services → Library**: enable the **Google Health API**.
3. **APIs & Services → OAuth consent screen** (Google Auth Platform):
   - User type **External**, publishing status **Testing**.
   - Add your own Google account (the one your Fitbit data is in) under
     **Test users**.
   - Add these scopes:
     - `.../auth/googlehealth.activity_and_fitness.readonly`
     - `.../auth/googlehealth.location.readonly`
     - `.../auth/googlehealth.health_metrics_and_measurements.readonly`
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type **Web application**.
   - Authorized redirect URI: `http://localhost:8765/`
   - Download the JSON and save it as
     `~/.config/ricelo/google-oauth-client.json` (keep it private:
     `chmod 600`). You can set `GOOGLE_HEALTH_CLIENT_ID` and
     `GOOGLE_HEALTH_CLIENT_SECRET` instead.
5. Log in once:
   ```bash
   ricelo google-login
   ```
   A browser opens. Allow access, and the token is saved to
   `~/.config/ricelo/google-health-token.json`. On a machine without a
   browser, run `ricelo google-login --manual`. Open the URL anywhere and,
   after you allow access, paste back the `http://localhost:8765/?...` URL
   the browser lands on. That page doesn't load, which is expected.
6. Check that your Garmin key works: `ricelo auth`.

## Running it

```bash
ricelo sync-fitbit --dry-run --save-dir ./tcx   # preview: export and prepare, upload nothing
ricelo sync-fitbit                              # last 7 days
ricelo sync-fitbit --since 2026-01-01           # backfill
ricelo sync-fitbit -t RUNNING -t WALKING        # only some exercise types
```

By default an exercise is **skipped when Garmin already has an activity that
covers the same time**, for example a run recorded by both watches. Use
`--include-overlapping` to upload it anyway.

### Automatic sync

Run it on a schedule, for example every hour with cron:

```cron
17 * * * * $HOME/.local/bin/ricelo sync-fitbit -d 3 >> $HOME/.cache/ricelo-sync.log 2>&1
```

On macOS you can use a launchd agent, and on Windows a Task Scheduler task
with the same command.

## Limitations

- **Weekly re-login.** While your OAuth app is in *Testing* mode, Google's
  refresh tokens expire after 7 days, so you need to run `ricelo google-login`
  again each week. The sync then says the refresh token was rejected. Publishing
  the app would avoid this, but Google Health scopes are *restricted*, and
  publishing means a privacy and security review by Google.
- **Garmin upload through the Personal API is probably not allowed.** The
  [Garmin Connect Developer Program overview](https://developer.garmin.com/gc-developer-program/overview/)
  lists the Personal tier (the key ricelo uses) as *"Pull only"*: it reads your
  own Activity, Health and Women's Health data. The Business tiers are *"Push"*
  from Garmin to a partner, and none of the tiers is described as accepting
  completed activities. ricelo still tries Garmin's standard upload endpoint
  (`/upload-service/upload`) in case the key allows it. If Garmin refuses
  (HTTP 401, 403, 404 or 405), the sync stops with "Upload not allowed". Use
  the file route instead:
  ```bash
  ricelo sync-fitbit --dry-run --save-dir ~/fitbit-tcx
  ```
  Then import the files at connect.garmin.com → Import Data, which accepts
  several files at once. The dry run doesn't update the state file, so a later
  run lists the same exercises again. Use `--since` to limit it to new ones.
- **Sport types.** TCX only knows Running, Biking and Other. Walks, hikes,
  gym sessions and the like arrive as "Other". You can change the type in
  Garmin Connect.
- **Only exercises.** Steps, sleep, resting heart rate and other all-day
  data are not copied. Garmin Connect has no way to import them.
- **Exercises without GPS** (treadmill, gym) are exported with
  `partialData=true`, so they keep their time and heart-rate samples.
