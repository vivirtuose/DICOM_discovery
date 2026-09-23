# Releasing DICOM_discovery

Publishing is deliberately **human-gated at two points**: pushing a tag only produces a
*draft* release, and only publishing that draft (or running the workflow by hand) uploads to
PyPI. A PyPI version number can never be re-used, even after deletion.

## One-time setup — PyPI Trusted Publishing

No API token is stored in this repository; GitHub authenticates to PyPI over OIDC.

1. Sign in on <https://pypi.org> → *Your projects* → *Publishing* → **Add a new pending publisher**
   (the project does not exist yet — that is what "pending" means):
   - PyPI project name: `dicom-discovery` *(free as of 2026-09-22)*
   - Owner: `vivirtuose` · Repository: `DICOM_discovery`
   - Workflow name: `publish-pypi.yml`
   - Environment name: `pypi`
2. Optional but recommended: repeat on <https://test.pypi.org> with environment `testpypi`,
   and rehearse with `workflow_dispatch` → *index: testpypi* before the real thing.
3. Optional: in GitHub → *Settings* → *Environments* → `pypi`, add yourself as a required
   reviewer, so every upload needs one more click.

## Releasing a version

```bash
# 1. bump the version in BOTH files (a CI job refuses a tag that disagrees)
#    DICOM_discovery/pyproject.toml            version = "X.Y.Z"
#    DICOM_discovery/src/DICOM_discovery/__init__.py   __version__ = "X.Y.Z"
#    …and the deployment defaults a test checks: deploy/nas/docker-compose.yml, .env.example
# 2. write the CHANGELOG entry
# 3. commit, push, wait for CI to be green on master
git tag vX.Y.Z && git push origin vX.Y.Z
```

The **Release** workflow then:

1. refuses the tag if it does not match `pyproject.toml`;
2. builds the sdist + wheel, runs `twine check`, installs the wheel in a clean venv and runs
   `--version`, `doctor`, `demo` and a full `job` end to end;
3. rebuilds the NAS bundle (Docker images amd64/arm64 + offline wheelhouses, each smoke-tested);
4. collects everything with a `SHA256SUMS.txt` into a **draft** GitHub release.

Review the draft, then **Publish** it. That fires `publish-pypi.yml`, which rebuilds from the
tag and uploads to PyPI via Trusted Publishing.

## Published releases

The first release, **v0.11.0 (2026-09-23)**, is on PyPI as
[`DICOM-discovery`](https://pypi.org/project/DICOM-discovery/) — `pip install dicom-discovery`
works regardless of case and separator, since PyPI normalises project names.

## What is *not* automated, on purpose

- Nothing is pushed to a container registry (GHCR) — the images travel as release assets and
  Actions artifacts, which suits an air-gapped hospital network.
- No auto-merge, no auto-publish on push: a release is always a deliberate act.
