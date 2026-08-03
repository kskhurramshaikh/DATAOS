<<<<<<< HEAD
# DataOS 2.0 -- Pipeline Rails (Phase One Test)

This is the first working slice of the DataOS 2.0 agentic pipeline:

```
POST /intent  -->  compliance agent  -->  smart router  -->  adapter  -->  tool  -->  rendered output
```

One intent is wired up end to end -- **`validate_drift`**, backed by the
**Evidently AI** capability (`compute_data_model_drift`) -- chosen because
it's already proven to work (harness-tested earlier in this engagement)
and needs no external infrastructure to run. Every future intent from the
32 listed in the DataOS 2.0 Capability Corpus doc gets added the same
way, one at a time: a capability registry entry + an adapter + a test,
not all at once.

## What's actually proven here

- 4 automated tests, all passing: health check, an unregistered-intent
  error path, a compliance-blocked path (RESTRICTED data, no override),
  and a full success path with real drift detection (not a stub -- it
  genuinely computes drift and asserts a low p-value on the shifted
  feature).
- The same pipeline was also run as a live HTTP server and hit with
  real `curl` requests, not just the test client -- both the success and
  blocked paths came back correctly over the wire.

## Project layout

```
app/
  main.py                    FastAPI entrypoint -- the only intent-capture surface
  compliance_agent.py        Hard-rule gate, evaluated before routing
  router.py                  Looks up the capability registry, dispatches to an adapter
  capability_registry.py     intent -> capability -> tool -> adapter mapping
  adapters/
    evidently_adapter.py     The only file that knows Evidently AI exists
tests/
  test_pipeline.py           End-to-end tests for the wired-up intent
Dockerfile                   Container build for Render
render.yaml                  Render Blueprint (service config)
.github/workflows/
  ci-deploy.yml              Test on every push; deploy to Render only if tests pass
```

## Running it locally

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/pytest tests/ -v

# run the live server
./venv/bin/uvicorn app.main:app --reload

# try it
curl -X POST http://127.0.0.1:8000/intent -H "Content-Type: application/json" -d '{
  "intent": "validate_drift",
  "context": {"dataset_classification": "INTERNAL"},
  "payload": {"drift_feature": "mean radius", "shift_multiplier": 1.6, "shift_offset": 3}
}'
```

## Setting up the rails (the two steps that need your accounts)

Everything above is built and tested. These two steps can't be done for
you -- they need your own GitHub and Render logins.

### 1. Create the GitHub repository

```bash
cd dataos-pipeline   # this folder
git init
git add .
git commit -m "Pipeline rails: first intent (validate_drift) wired end to end"
git branch -M main
git remote add origin https://github.com/<your-org>/<repo-name>.git
git push -u origin main
```

(Create the empty repo on GitHub first via the web UI or `gh repo create`,
then run the above.)

### 2. Connect Render

1. In the Render dashboard: **New > Blueprint**, point it at this repo.
   Render will read `render.yaml` automatically and provision the
   service (name: `dataos-2-0-pipeline`).
2. Once the service exists, go to its **Settings > Deploy Hook** and
   copy the deploy hook URL.
3. In the GitHub repo: **Settings > Secrets and variables > Actions**,
   add a new secret named `RENDER_DEPLOY_HOOK_URL` with that value.
4. That's it. From then on: push to `main` -> GitHub Actions runs the
   test suite -> if it passes, it calls the Render deploy hook -> Render
   builds the Docker image and deploys. If tests fail, nothing deploys.

Until step 3 is done, the workflow's deploy job will run and skip
itself cleanly (it checks for the secret first) rather than failing --
so it's safe to push before Render is connected.

## Adding the next intent

1. Pick the next intent from the 32 in scope (DataOS 2.0 doc, Section 5).
2. Write an adapter in `app/adapters/` that calls the real tool for that
   capability and returns a plain dict -- same shape as
   `evidently_adapter.py`.
3. Add an entry to `CAPABILITY_REGISTRY` in `app/capability_registry.py`.
4. Add compliance rules to `app/compliance_agent.py` if this intent
   needs its own governance checks beyond the existing ones.
5. Write tests proving it end to end, the same way
   `tests/test_pipeline.py` does for `validate_drift`.
6. Push. CI tests it, then deploys it, automatically.
=======
