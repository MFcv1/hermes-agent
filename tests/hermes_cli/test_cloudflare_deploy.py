from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hermes_cli.cloudflare_deploy import (
    CloudflareDeployError,
    CommandResult,
    _artifact_input_digest,
    build_artifact,
    deploy_manifest,
    load_and_verify_manifest,
    rollback_manifest,
    smoke_manifest,
    validate_contract,
)


SHA = "a" * 40
ACCOUNT = "0123456789abcdef0123456789abcdef"
VERSION_OLD = "11111111-1111-1111-1111-111111111111"
VERSION_NEW = "22222222-2222-2222-2222-222222222222"


def _project(tmp_path: Path) -> tuple[Path, dict, dict[str, str]]:
    project = tmp_path / "portfolio"
    project.mkdir()
    (project / "wrangler.jsonc").write_text(
        json.dumps(
            {
                "name": "portfolio",
                "account_id": ACCOUNT,
                "main": ".open-next/worker.js",
                "compatibility_flags": ["nodejs_compat"],
                "assets": {"directory": ".open-next/assets", "binding": "ASSETS"},
                "vars": {"NEXT_PUBLIC_SITE_URL": "https://portfolio.example"},
            }
        ),
        encoding="utf-8",
    )
    (project / "open-next.config.ts").write_text(
        "export default { enableCacheInterception: false };\n", encoding="utf-8"
    )
    contract = {
        "provider": {
            "worker_name": "portfolio",
            "account_id": ACCOUNT,
            "plan": "free",
            "max_compressed_bytes": 3 * 1024 * 1024,
        },
        "build": {
            "required_env": ["NEXT_PUBLIC_SITE_URL"],
            "public_origin_env": "NEXT_PUBLIC_SITE_URL",
        },
        "runtime": {
            "required_vars": ["NEXT_PUBLIC_SITE_URL"],
            "required_secrets": [],
        },
        "cache": {"mode": "static_assets"},
        "commands": {
            "next_build": ["next-build"],
            "open_next_build": ["open-next-build", "--skipNextBuild"],
            "wrangler": ["wrangler"],
        },
        "smoke": {
            "base_url": "https://portfolio.example",
            "build_info_path": "/__hermes/build-info.json",
            "checks": [
                {
                    "path": "/",
                    "status": 200,
                    "contains": ["Portfolio"],
                    "canonical": "/",
                    "content_type": "text/html",
                    "cache_control_contains": "public",
                },
                {
                    "path": "/robots.txt",
                    "status": 200,
                    "not_contains": ["localhost"],
                },
                {"path": "/sitemap.xml", "status": 200, "contains": ["<urlset"]},
            ],
        },
    }
    return project, contract, {"NEXT_PUBLIC_SITE_URL": "https://portfolio.example"}


def _manifest(project: Path, *, source_commit: str = SHA) -> Path:
    artifact = project / "artifact"
    assets = artifact / "open-next" / "assets" / "__hermes"
    assets.mkdir(parents=True)
    (artifact / "open-next" / "worker.js").write_text("export default {};\n", encoding="utf-8")
    (artifact / "wrangler.json").write_text("{}\n", encoding="utf-8")
    digest = _artifact_input_digest(artifact)
    (assets / "build-info.json").write_text(
        json.dumps({"source_commit": source_commit, "artifact_digest": digest}) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "artifact_dir": str(artifact),
        "artifact_digest": digest,
        "source_commit": source_commit,
        "account_id": ACCOUNT,
        "worker_name": "portfolio",
        "smoke_base_url": "https://portfolio.example",
    }
    path = artifact / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _init_git(project: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "-qm", "init",
        ],
        cwd=project,
        check=True,
    )
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()


def test_validate_rejects_missing_build_variable(tmp_path: Path) -> None:
    project, contract, _ = _project(tmp_path)

    with pytest.raises(CloudflareDeployError, match="missing build variables"):
        validate_contract(project, contract, env={})


def test_validate_rejects_dummy_cache_and_wrong_account(tmp_path: Path) -> None:
    project, contract, env = _project(tmp_path)
    (project / "open-next.config.ts").write_text("export default { cache: 'dummy' };\n")

    with pytest.raises(CloudflareDeployError, match="dummy OpenNext cache"):
        validate_contract(project, contract, env=env)

    (project / "open-next.config.ts").write_text("export default {};\n")
    with pytest.raises(CloudflareDeployError, match="account does not match"):
        validate_contract(project, contract, env=env, identity_output="Account ID: wrong")


def test_prepare_builds_next_then_opennext_and_restores_local_outputs(tmp_path: Path) -> None:
    project, contract, env = _project(tmp_path)
    (project / ".next").mkdir()
    (project / ".next" / "old").write_text("old-next")
    (project / ".open-next" / "assets").mkdir(parents=True)
    (project / ".open-next" / "old").write_text("old-open-next")
    calls: list[tuple[str, ...]] = []

    def runner(argv, cwd, command_env, timeout):
        calls.append(tuple(argv))
        if argv[:2] == ["git", "rev-parse"]:
            return CommandResult(0, SHA + "\n")
        if argv[:2] == ["git", "status"]:
            return CommandResult(0, "")
        if argv[0] == "next-build":
            (cwd / ".next").mkdir()
            (cwd / ".next" / "BUILD_ID").write_text("fresh")
            return CommandResult(0, "")
        if argv[0] == "open-next-build":
            (cwd / ".open-next" / "assets").mkdir(parents=True)
            (cwd / ".open-next" / "worker.js").write_text("fresh worker")
            (cwd / ".open-next" / "assets" / "app.js").write_text("fresh asset")
            return CommandResult(0, "")
        if argv[:3] == ["wrangler", "deploy", "--dry-run"]:
            outdir = Path(argv[argv.index("--outdir") + 1])
            outdir.mkdir(parents=True)
            (outdir / "bundle.js").write_text("bundle")
            return CommandResult(0, "Total Upload: 1 KiB / gzip: 0.5 KiB")
        raise AssertionError(argv)

    manifest_path = build_artifact(
        project,
        contract,
        artifacts_dir=project / ".hermes-deploy",
        runner=runner,
        env=env,
    )

    manifest = load_and_verify_manifest(manifest_path)
    assert calls.index(("next-build",)) < calls.index(("open-next-build", "--skipNextBuild"))
    assert (project / ".next" / "old").read_text() == "old-next"
    assert (project / ".open-next" / "old").read_text() == "old-open-next"
    assert (manifest_path.parent / "open-next" / "worker.js").read_text() == "fresh worker"
    assert manifest["source_commit"] == SHA
    assert manifest["environment"]["runtime_vars"] == ["NEXT_PUBLIC_SITE_URL"]
    assert manifest["cache"] == {"mode": "static_assets"}


def test_manifest_rejects_stale_or_modified_artifact(tmp_path: Path) -> None:
    project, _, _ = _project(tmp_path)
    manifest = _manifest(project)
    (manifest.parent / "open-next" / "worker.js").write_text("stale worker")

    with pytest.raises(CloudflareDeployError, match="artifact digest mismatch"):
        load_and_verify_manifest(manifest)


@pytest.mark.parametrize(
    ("bad_path", "bad_body", "error"),
    [
        (
            "/robots.txt",
            b"User-agent: *\nSitemap: http://localhost/sitemap.xml",
            "smoke contract failed",
        ),
        (
            "/__hermes/build-info.json",
            json.dumps({"source_commit": "b" * 40}).encode(),
            "smoke contract failed",
        ),
    ],
)
def test_smoke_rejects_old_robots_or_divergent_sha(
    tmp_path: Path, bad_path: str, bad_body: bytes, error: str
) -> None:
    project, contract, _ = _project(tmp_path)
    manifest_path = _manifest(project)
    manifest = json.loads(manifest_path.read_text())

    def fetcher(url: str, timeout: int):
        path = "/" + url.split("/", 3)[3].split("?", 1)[0] if url.count("/") >= 3 else "/"
        if path == bad_path:
            return 200, {"content-type": "text/plain", "cache-control": "public"}, bad_body
        if path == "/":
            body = (
                b'<html><head><link rel="canonical" '
                b'href="https://portfolio.example/"></head>Portfolio</html>'
            )
            return 200, {"content-type": "text/html", "cache-control": "public, max-age=60"}, body
        if path == "/robots.txt":
            return (
                200,
                {"content-type": "text/plain"},
                b"User-agent: *\nSitemap: https://portfolio.example/sitemap.xml",
            )
        if path == "/sitemap.xml":
            return 200, {"content-type": "application/xml"}, b"<urlset></urlset>"
        if path == "/__hermes/build-info.json":
            return 200, {"content-type": "application/json"}, json.dumps(
                {
                    "source_commit": manifest["source_commit"],
                    "artifact_digest": manifest["artifact_digest"],
                }
            ).encode()
        raise AssertionError(path)

    with pytest.raises(CloudflareDeployError, match=error):
        smoke_manifest(manifest_path, contract, fetcher=fetcher)


def test_deploy_records_provider_version_and_rollback_uses_previous_version(tmp_path: Path) -> None:
    project, contract, env = _project(tmp_path)
    commit = _init_git(project)
    manifest_path = _manifest(project, source_commit=commit)
    subprocess.run(["git", "add", "artifact"], cwd=project, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "-qm", "artifact",
        ],
        cwd=project,
        check=True,
    )
    current = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    data = json.loads(manifest_path.read_text())
    data["source_commit"] = current
    manifest_path.write_text(json.dumps(data))
    attestation = manifest_path.parent / "open-next" / "assets" / "__hermes" / "build-info.json"
    proof = json.loads(attestation.read_text())
    proof["source_commit"] = current
    attestation.write_text(json.dumps(proof))
    calls: list[tuple[str, ...]] = []

    def runner(argv, cwd, command_env, timeout):
        calls.append(tuple(argv))
        if argv[:2] == ["wrangler", "whoami"]:
            return CommandResult(0, f"Account ID: {ACCOUNT}")
        if argv[:3] == ["wrangler", "secret", "list"]:
            return CommandResult(0, "[]")
        if argv[:3] == ["wrangler", "deployments", "list"]:
            return CommandResult(0, json.dumps([{"version_id": VERSION_OLD}]))
        if argv[:2] == ["wrangler", "deploy"]:
            assert cwd == manifest_path.parent
            assert str(manifest_path.parent / "wrangler.json") in argv
            return CommandResult(0, f"Version ID: {VERSION_NEW}\nhttps://portfolio.workers.dev")
        if argv[:2] == ["wrangler", "rollback"]:
            return CommandResult(0, "rolled back")
        raise AssertionError(argv)

    receipt_path = deploy_manifest(
        manifest_path, project, contract, confirm_upload=True, runner=runner, env=env
    )
    receipt = json.loads(receipt_path.read_text())
    assert receipt["previous_version_id"] == VERSION_OLD
    assert receipt["deployed_version_id"] == VERSION_NEW

    rollback = rollback_manifest(
        manifest_path, project, contract, confirm_rollback=True, runner=runner, env=env
    )
    assert rollback["rolled_back_to"] == VERSION_OLD
    assert (manifest_path.parent / "rollback-receipt.json").is_file()
    assert any(call[:3] == ("wrangler", "rollback", VERSION_OLD) for call in calls)


def test_external_mutations_require_explicit_confirmation(tmp_path: Path) -> None:
    project, contract, env = _project(tmp_path)
    manifest = _manifest(project)

    with pytest.raises(CloudflareDeployError, match="confirm-upload"):
        deploy_manifest(manifest, project, contract, confirm_upload=False, env=env)
    with pytest.raises(CloudflareDeployError, match="confirm-rollback"):
        rollback_manifest(manifest, project, contract, confirm_rollback=False, env=env)
