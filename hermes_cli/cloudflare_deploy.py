"""Atomic, evidence-backed Cloudflare Workers deployment workflow."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml


class CloudflareDeployError(RuntimeError):
    """Fail-closed deployment contract violation."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str = ""


Runner = Callable[[Sequence[str], Path, Mapping[str, str], int], CommandResult]
_ATTESTATION_RELATIVE = Path("open-next/assets/__hermes/build-info.json")


def _default_runner(
    argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout: int
) -> CommandResult:
    completed = subprocess.run(
        list(argv), cwd=str(cwd), env=dict(env), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _run_checked(
    runner: Runner, argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout: int
) -> CommandResult:
    result = runner(argv, cwd, env, timeout)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "command failed")[-2000:]
        raise CloudflareDeployError(f"command failed ({' '.join(argv[:3])}): {tail}")
    return result


def load_contract(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CloudflareDeployError(f"deployment contract not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except Exception as exc:
        raise CloudflareDeployError(f"invalid deployment contract: {exc}") from exc
    if not isinstance(raw, dict):
        raise CloudflareDeployError("deployment contract must be an object")
    return raw


def _strip_jsonc(text: str) -> str:
    out: list[str] = []
    i = 0
    quoted = False
    escaped = False
    while i < len(text):
        char = text[i]
        if quoted:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            i += 1
            continue
        if char == '"':
            quoted = True
            out.append(char)
            i += 1
            continue
        if char == "/" and i + 1 < len(text) and text[i + 1] == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if char == "/" and i + 1 < len(text) and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            if end < 0:
                raise CloudflareDeployError("unterminated comment in wrangler JSONC")
            i = end + 2
            continue
        out.append(char)
        i += 1
    return re.sub(r",\s*([}\]])", r"\1", "".join(out))


def find_wrangler_config(project: Path) -> Path:
    for name in ("wrangler.json", "wrangler.jsonc", "wrangler.toml"):
        candidate = project / name
        if candidate.is_file():
            return candidate
    raise CloudflareDeployError("wrangler.json, wrangler.jsonc or wrangler.toml is required")


def load_wrangler_config(path: Path) -> dict[str, Any]:
    try:
        if path.suffix == ".toml":
            import tomllib
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        else:
            raw = json.loads(_strip_jsonc(path.read_text(encoding="utf-8")))
    except Exception as exc:
        raise CloudflareDeployError(f"invalid Wrangler config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CloudflareDeployError("Wrangler config must be an object")
    return raw


def _as_command(value: Any, default: str) -> list[str]:
    value = default if value in (None, "") else value
    if isinstance(value, str):
        parsed = shlex.split(value)
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        parsed = list(value)
    else:
        raise CloudflareDeployError("commands must be strings or string arrays")
    if not parsed:
        raise CloudflareDeployError("empty deployment command")
    return parsed


def _required_names(section: Mapping[str, Any], key: str) -> set[str]:
    values = section.get(key) or []
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise CloudflareDeployError(f"{key} must be a list of names")
    return {item for item in values if item}


def _public_origin(contract: Mapping[str, Any], env: Mapping[str, str]) -> tuple[str, str]:
    build = contract.get("build") or {}
    name = str(build.get("public_origin_env") or "NEXT_PUBLIC_SITE_URL")
    value = str(env.get(name) or build.get("public_origin") or "").strip()
    parsed = urllib.parse.urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not parsed.netloc:
        raise CloudflareDeployError(f"{name} must be an absolute HTTPS URL")
    if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".local"):
        raise CloudflareDeployError(f"{name} cannot point to a local origin")
    return name, value.rstrip("/")


def validate_contract(
    project: Path,
    contract: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    identity_output: str | None = None,
    remote_secret_names: set[str] | None = None,
) -> dict[str, Any]:
    env = dict(os.environ if env is None else env)
    provider = contract.get("provider") or {}
    worker_name = str(provider.get("worker_name") or "").strip()
    account_id = str(provider.get("account_id") or "").strip()
    plan = str(provider.get("plan") or "").lower()
    if not worker_name or not account_id:
        raise CloudflareDeployError("provider.worker_name and provider.account_id are required")
    if plan not in {"free", "paid"}:
        raise CloudflareDeployError("provider.plan must be free or paid")
    if identity_output is not None and account_id.lower() not in identity_output.lower():
        raise CloudflareDeployError("authenticated Cloudflare account does not match contract")

    build = contract.get("build") or {}
    missing_build = sorted(
        name for name in _required_names(build, "required_env") if not env.get(name)
    )
    if missing_build:
        raise CloudflareDeployError(f"missing build variables: {', '.join(missing_build)}")
    origin_name, origin = _public_origin(contract, env)

    wrangler_path = find_wrangler_config(project)
    wrangler = load_wrangler_config(wrangler_path)
    if str(wrangler.get("name") or "") != worker_name:
        raise CloudflareDeployError("Wrangler worker name does not match provider.worker_name")
    if "nodejs_compat" not in (wrangler.get("compatibility_flags") or []):
        raise CloudflareDeployError("Wrangler config must enable nodejs_compat")
    configured_account = str(wrangler.get("account_id") or "")
    if configured_account and configured_account != account_id:
        raise CloudflareDeployError("Wrangler account_id does not match provider.account_id")
    if str(wrangler.get("main") or "") != ".open-next/worker.js":
        raise CloudflareDeployError("Wrangler main must be .open-next/worker.js")
    assets_config = wrangler.get("assets") or {}
    if str(assets_config.get("directory") or "") != ".open-next/assets":
        raise CloudflareDeployError("Wrangler assets.directory must be .open-next/assets")
    runtime = contract.get("runtime") or {}
    runtime_vars = set((wrangler.get("vars") or {}).keys())
    missing_runtime = sorted(_required_names(runtime, "required_vars") - runtime_vars)
    if missing_runtime:
        raise CloudflareDeployError(
            f"missing runtime variables in Wrangler config: {', '.join(missing_runtime)}"
        )
    if origin_name in _required_names(runtime, "required_vars"):
        if str((wrangler.get("vars") or {}).get(origin_name) or "").rstrip("/") != origin:
            raise CloudflareDeployError(
                f"runtime {origin_name} must equal the build public origin"
            )
    required_secrets = _required_names(runtime, "required_secrets")
    if remote_secret_names is not None:
        missing_secrets = sorted(required_secrets - remote_secret_names)
        if missing_secrets:
            raise CloudflareDeployError(f"missing Cloudflare secrets: {', '.join(missing_secrets)}")

    cache = contract.get("cache") or {}
    if str(cache.get("mode") or "") != "static_assets":
        raise CloudflareDeployError("cache.mode must be static_assets for this workflow")
    open_next_config = next(
        (
            project / name
            for name in (
                "open-next.config.ts",
                "open-next.config.js",
                "open-next.config.mjs",
            )
            if (project / name).is_file()
        ),
        None,
    )
    if open_next_config:
        config_text = open_next_config.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\bdummy\b", config_text, re.IGNORECASE):
            raise CloudflareDeployError("dummy OpenNext cache is forbidden")
        if re.search(r"enableCacheInterception\s*:\s*true", config_text):
            raise CloudflareDeployError("response cache interception must be disabled")

    smoke = contract.get("smoke") or {}
    base_url = str(smoke.get("base_url") or origin).rstrip("/")
    checks = smoke.get("checks")
    if not isinstance(checks, list) or not checks:
        raise CloudflareDeployError("smoke.checks must contain at least one route")
    if not all(isinstance(check, dict) and check.get("path") for check in checks):
        raise CloudflareDeployError("each smoke check must be an object with a path")
    if smoke.get("build_info_path") != "/__hermes/build-info.json":
        raise CloudflareDeployError(
            "smoke.build_info_path must be /__hermes/build-info.json"
        )
    return {
        "worker_name": worker_name, "account_id": account_id, "plan": plan,
        "public_origin_env": origin_name, "public_origin": origin,
        "wrangler_path": str(wrangler_path), "wrangler": wrangler, "base_url": base_url,
    }


def _git_state(project: Path, runner: Runner, env: Mapping[str, str]) -> tuple[str, bool]:
    commit = _run_checked(runner, ["git", "rev-parse", "HEAD"], project, env, 30).stdout.strip()
    dirty = bool(
        _run_checked(
            runner, ["git", "status", "--porcelain"], project, env, 30
        ).stdout.strip()
    )
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", commit):
        raise CloudflareDeployError("could not resolve a full git commit")
    return commit, dirty


def _restore_output(path: Path, backup: Path | None) -> None:
    if path.exists():
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    if backup is not None and backup.exists():
        backup.rename(path)


def _write_artifact_config(source: Mapping[str, Any], artifact: Path) -> Path:
    config = json.loads(json.dumps(source, default=str))
    config["main"] = "open-next/worker.js"
    assets = config.get("assets") if isinstance(config.get("assets"), dict) else {}
    assets["directory"] = "open-next/assets"
    assets.setdefault("binding", "ASSETS")
    config["assets"] = assets
    path = artifact / "wrangler.json"
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _compressed_size_from_dry_run(output: str, dry_run_dir: Path) -> int:
    match = re.search(r"gzip:\s*([0-9.]+)\s*(KiB|MiB|B)", output, re.IGNORECASE)
    if match:
        value = float(match.group(1))
        return int(value * {"b": 1, "kib": 1024, "mib": 1024 * 1024}[match.group(2).lower()])
    files = [item for item in dry_run_dir.rglob("*") if item.is_file()]
    if not files:
        raise CloudflareDeployError("Wrangler dry-run produced no measurable bundle")
    return len(gzip.compress(b"".join(item.read_bytes() for item in sorted(files))))


def build_artifact(
    project: Path,
    contract: Mapping[str, Any],
    *,
    artifacts_dir: Path,
    allow_dirty: bool = False,
    runner: Runner = _default_runner,
    env: Mapping[str, str] | None = None,
) -> Path:
    project = project.resolve()
    env_map = dict(os.environ if env is None else env)
    validated = validate_contract(project, contract, env=env_map)
    env_map[validated["public_origin_env"]] = validated["public_origin"]
    commit, dirty = _git_state(project, runner, env_map)
    if dirty and not allow_dirty:
        raise CloudflareDeployError("source tree is dirty; commit changes or pass --allow-dirty")

    run_id = f"cf_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{uuid.uuid4().hex[:8]}"
    artifact = artifacts_dir.resolve() / "artifacts" / run_id
    if artifact.exists():
        raise CloudflareDeployError(f"artifact already exists: {artifact}")
    artifact.mkdir(parents=True)
    commands = contract.get("commands") or {}
    next_command = _as_command(commands.get("next_build"), "npm run build")
    open_next_command = _as_command(
        commands.get("open_next_build"),
        "npx opennextjs-cloudflare build --skipNextBuild",
    )
    wrangler_command = _as_command(commands.get("wrangler"), "npx wrangler")

    next_output = project / ".next"
    open_next_output = project / ".open-next"
    with tempfile.TemporaryDirectory(prefix="hermes-cf-backup-", dir=str(project.parent)) as tmp:
        temp = Path(tmp)
        next_backup = temp / "next.previous" if next_output.exists() else None
        open_backup = temp / "open-next.previous" if open_next_output.exists() else None
        if next_backup:
            next_output.rename(next_backup)
        if open_backup:
            open_next_output.rename(open_backup)
        try:
            _run_checked(runner, next_command, project, env_map, 1800)
            if not next_output.is_dir():
                raise CloudflareDeployError("Next build did not create .next")
            _run_checked(runner, open_next_command, project, env_map, 1800)
            if not (open_next_output / "worker.js").is_file():
                raise CloudflareDeployError("OpenNext build did not create .open-next/worker.js")
            if not (open_next_output / "assets").is_dir():
                raise CloudflareDeployError("OpenNext build did not create .open-next/assets")
            shutil.copytree(open_next_output, artifact / "open-next")
        finally:
            _restore_output(next_output, next_backup)
            _restore_output(open_next_output, open_backup)

    artifact_config = _write_artifact_config(validated["wrangler"], artifact)
    attestation_path = artifact / _ATTESTATION_RELATIVE
    attestation_path.parent.mkdir(parents=True, exist_ok=True)
    attestation_path.write_text(
        json.dumps({"source_commit": commit.lower(), "artifact_digest": "pending"}) + "\n",
        encoding="utf-8",
    )
    dry_run_dir = artifact / "wrangler-dry-run"
    dry_run = _run_checked(
        runner,
        wrangler_command + [
            "deploy", "--dry-run", "--outdir", str(dry_run_dir),
            "--config", str(artifact_config),
        ],
        artifact,
        env_map,
        600,
    )
    compressed_size = _compressed_size_from_dry_run(
        dry_run.stdout + dry_run.stderr, dry_run_dir
    )
    provider = contract.get("provider") or {}
    default_limit = 3 * 1024 * 1024 if validated["plan"] == "free" else 10 * 1024 * 1024
    limit = int(provider.get("max_compressed_bytes") or default_limit)
    if compressed_size > limit:
        raise CloudflareDeployError(
            f"compressed Worker exceeds configured {validated['plan']} limit: "
            f"{compressed_size}>{limit}"
        )
    shutil.rmtree(dry_run_dir, ignore_errors=True)
    digest = _artifact_input_digest(artifact)
    attestation_path.write_text(
        json.dumps({
            "schema_version": 1,
            "source_commit": commit.lower(),
            "artifact_digest": digest,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": "cloudflare-workers",
        "account_id": validated["account_id"],
        "worker_name": validated["worker_name"],
        "plan": validated["plan"],
        "source_commit": commit.lower(),
        "source_dirty": dirty,
        "public_origin": validated["public_origin"],
        "environment": {
            "build_required": sorted(_required_names(contract.get("build") or {}, "required_env")),
            "runtime_vars": sorted(_required_names(contract.get("runtime") or {}, "required_vars")),
            "runtime_secrets": sorted(
                _required_names(contract.get("runtime") or {}, "required_secrets")
            ),
            "public_origin_env": validated["public_origin_env"],
        },
        "cache": dict(contract.get("cache") or {}),
        "wrangler_config_digest": hashlib.sha256(
            artifact_config.read_bytes()
        ).hexdigest(),
        "artifact_digest": digest,
        "compressed_size": compressed_size,
        "compressed_limit": limit,
        "artifact_dir": str(artifact),
        "config": str(artifact_config),
        "smoke_base_url": validated["base_url"],
    }
    manifest_path = artifact / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest_path


def _artifact_input_digest(artifact: Path) -> str:
    digest = hashlib.sha256()
    targets = [artifact / "wrangler.json"] + sorted(
        item for item in (artifact / "open-next").rglob("*")
        if item.is_file() and item != artifact / _ATTESTATION_RELATIVE
    )
    for target in targets:
        if not target.is_file():
            raise CloudflareDeployError(f"artifact input missing: {target.name}")
        rel = target.relative_to(artifact).as_posix().encode()
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        digest.update(target.read_bytes())
    return digest.hexdigest()


def load_and_verify_manifest(path: Path, *, project: Path | None = None) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CloudflareDeployError(f"invalid artifact manifest: {exc}") from exc
    artifact = Path(str(manifest.get("artifact_dir") or "")).resolve()
    if path.resolve().parent != artifact:
        raise CloudflareDeployError("manifest path and artifact_dir diverge")
    if _artifact_input_digest(artifact) != str(manifest.get("artifact_digest") or ""):
        raise CloudflareDeployError("artifact digest mismatch; refusing stale or modified output")
    try:
        attestation = json.loads((artifact / _ATTESTATION_RELATIVE).read_text(encoding="utf-8"))
    except Exception as exc:
        raise CloudflareDeployError(
            "artifact build-info attestation is missing or invalid"
        ) from exc
    if (
        attestation.get("source_commit") != manifest.get("source_commit")
        or attestation.get("artifact_digest") != manifest.get("artifact_digest")
    ):
        raise CloudflareDeployError("build-info attestation diverges from manifest")
    if project is not None:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(project), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        current = completed.stdout.strip().lower()
        if completed.returncode or current != str(manifest.get("source_commit") or "").lower():
            raise CloudflareDeployError("source HEAD differs from artifact manifest commit")
    return manifest


def _extract_version_id(data: Any) -> str | None:
    if isinstance(data, dict):
        for key in ("version_id", "versionId", "id"):
            value = data.get(key)
            if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F-]{16,}", value):
                return value
        for value in data.values():
            found = _extract_version_id(value)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _extract_version_id(value)
            if found:
                return found
    return None


def _wrangler_base(contract: Mapping[str, Any]) -> list[str]:
    return _as_command((contract.get("commands") or {}).get("wrangler"), "npx wrangler")


def online_identity(
    project: Path,
    contract: Mapping[str, Any],
    *,
    runner: Runner,
    env: Mapping[str, str],
) -> tuple[str, set[str]]:
    base = _wrangler_base(contract)
    identity = _run_checked(runner, base + ["whoami"], project, env, 120).stdout
    worker = str((contract.get("provider") or {}).get("worker_name") or "")
    secrets_result = _run_checked(
        runner,
        base + ["secret", "list", "--name", worker, "--format", "json"],
        project,
        env,
        120,
    )
    try:
        secret_data = json.loads(secrets_result.stdout or "[]")
        names = {
            str(item.get("name")) for item in secret_data
            if isinstance(item, dict) and item.get("name")
        }
    except json.JSONDecodeError as exc:
        raise CloudflareDeployError("could not parse Cloudflare secret inventory") from exc
    return identity, names


def deploy_manifest(
    manifest_path: Path,
    project: Path,
    contract: Mapping[str, Any],
    *,
    confirm_upload: bool,
    runner: Runner = _default_runner,
    env: Mapping[str, str] | None = None,
) -> Path:
    if not confirm_upload:
        raise CloudflareDeployError("external upload requires --confirm-upload")
    env_map = dict(os.environ if env is None else env)
    manifest = load_and_verify_manifest(manifest_path, project=project)
    identity, secrets = online_identity(project, contract, runner=runner, env=env_map)
    validated = validate_contract(
        project, contract, env=env_map, identity_output=identity,
        remote_secret_names=secrets,
    )
    if (
        validated["account_id"] != manifest.get("account_id")
        or validated["worker_name"] != manifest.get("worker_name")
    ):
        raise CloudflareDeployError("provider identity differs from artifact manifest")
    base = _wrangler_base(contract)
    prior = _run_checked(
        runner,
        base + ["deployments", "list", "--name", validated["worker_name"], "--json"],
        project,
        env_map,
        120,
    )
    try:
        previous_version = _extract_version_id(json.loads(prior.stdout or "[]"))
    except json.JSONDecodeError:
        previous_version = None
    artifact = manifest_path.resolve().parent
    message = f"hermes {manifest['source_commit'][:12]} digest={manifest['artifact_digest'][:16]}"
    deployed = _run_checked(
        runner,
        base + [
            "deploy", "--config", str(artifact / "wrangler.json"), "--keep-vars",
            "--message", message, "--experimental-auto-create=false",
        ],
        artifact,
        env_map,
        900,
    )
    output = deployed.stdout + "\n" + deployed.stderr
    version_match = re.search(
        r"(?:Version ID|version_id)\s*[:=]\s*([0-9a-fA-F-]{16,})", output
    )
    url_match = re.search(r"https://[^\s]+\.workers\.dev(?:/[^\s]*)?", output)
    deployed_version = version_match.group(1) if version_match else None
    if not deployed_version:
        versions = _run_checked(
            runner,
            base + ["versions", "list", "--name", validated["worker_name"], "--json"],
            project,
            env_map,
            120,
        )
        try:
            deployed_version = _extract_version_id(json.loads(versions.stdout or "[]"))
        except json.JSONDecodeError as exc:
            raise CloudflareDeployError(
                "deployment completed but provider version receipt was not parseable"
            ) from exc
    if not deployed_version:
        raise CloudflareDeployError(
            "deployment completed but Cloudflare returned no version id"
        )
    receipt = {
        "schema_version": 1,
        "deployed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_commit": manifest["source_commit"],
        "artifact_digest": manifest["artifact_digest"],
        "worker_name": manifest["worker_name"],
        "account_id": manifest["account_id"],
        "previous_version_id": previous_version,
        "deployed_version_id": deployed_version,
        "deployment_url": (
            url_match.group(0).rstrip(".,") if url_match else manifest.get("smoke_base_url")
        ),
        "output_summary": output[-2000:],
    }
    path = artifact / "deployment-receipt.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class _CanonicalParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.canonical: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "link" and str(values.get("rel") or "").lower() == "canonical":
            self.canonical = values.get("href")


def _fetch(url: str, timeout: int) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Hermes-Cloudflare-Smoke/1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (
                response.status,
                {key.lower(): value for key, value in response.headers.items()},
                response.read(2_000_000),
            )
    except urllib.error.HTTPError as exc:
        return (
            exc.code,
            {key.lower(): value for key, value in exc.headers.items()},
            exc.read(2_000_000),
        )


def smoke_manifest(
    manifest_path: Path,
    contract: Mapping[str, Any],
    *,
    base_url: str | None = None,
    fetcher: Callable[[str, int], tuple[int, dict[str, str], bytes]] = _fetch,
) -> dict[str, Any]:
    manifest = load_and_verify_manifest(manifest_path)
    smoke = contract.get("smoke") or {}
    root = str(
        base_url or smoke.get("base_url") or manifest.get("smoke_base_url") or ""
    ).rstrip("/")
    if not root.startswith("https://"):
        raise CloudflareDeployError("smoke base URL must be HTTPS")
    timeout = int(smoke.get("timeout_seconds") or 20)
    results: list[dict[str, Any]] = []
    for check in smoke.get("checks") or []:
        path = str(check.get("path") or "/")
        query = urllib.parse.urlencode({"__hermes_smoke": manifest["artifact_digest"][:12]})
        url = f"{root}{path}{'&' if '?' in path else '?'}{query}"
        status, headers, body = fetcher(url, timeout)
        text = body.decode("utf-8", errors="replace")
        errors: list[str] = []
        expected_status = int(check.get("status") or 200)
        if status != expected_status:
            errors.append(f"HTTP {status} != {expected_status}")
        for needle in check.get("contains") or []:
            if str(needle) not in text:
                errors.append(f"missing content: {needle}")
        for needle in check.get("not_contains") or []:
            if str(needle) in text:
                errors.append(f"forbidden content: {needle}")
        expected_type = check.get("content_type")
        if expected_type and str(expected_type).lower() not in headers.get(
            "content-type", ""
        ).lower():
            errors.append("content-type mismatch")
        if check.get("canonical"):
            parser = _CanonicalParser()
            parser.feed(text)
            expected_canonical = root + str(check["canonical"])
            if parser.canonical != expected_canonical:
                errors.append(f"canonical mismatch: {parser.canonical!r}")
        expected_cache = check.get("cache_control_contains")
        if expected_cache and str(expected_cache).lower() not in headers.get(
            "cache-control", ""
        ).lower():
            errors.append("cache-control mismatch")
        results.append({"path": path, "status": status, "ok": not errors, "errors": errors})

    build_info_path = str(smoke.get("build_info_path") or "")
    proof_url = (
        f"{root}{build_info_path}?__hermes_smoke={manifest['artifact_digest'][:12]}"
    )
    status, _, body = fetcher(proof_url, timeout)
    build_text = body.decode("utf-8", errors="replace")
    proof_ok = (
        status == 200
        and manifest["source_commit"] in build_text
        and manifest["artifact_digest"] in build_text
    )
    results.append({
        "path": build_info_path,
        "status": status,
        "ok": proof_ok,
        "errors": [] if proof_ok else ["build-info does not attest manifest SHA and digest"],
    })
    report = {
        "ok": all(item["ok"] for item in results),
        "base_url": root,
        "source_commit": manifest["source_commit"],
        "artifact_digest": manifest["artifact_digest"],
        "checks": results,
    }
    path = manifest_path.resolve().parent / "smoke-report.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not report["ok"]:
        raise CloudflareDeployError(f"smoke contract failed; report: {path}")
    return report


def rollback_manifest(
    manifest_path: Path,
    project: Path,
    contract: Mapping[str, Any],
    *,
    confirm_rollback: bool,
    runner: Runner = _default_runner,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if not confirm_rollback:
        raise CloudflareDeployError("rollback requires --confirm-rollback")
    manifest = load_and_verify_manifest(manifest_path, project=project)
    receipt_path = manifest_path.resolve().parent / "deployment-receipt.json"
    if not receipt_path.is_file():
        raise CloudflareDeployError("deployment receipt missing; rollback target is unknown")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    version = str(receipt.get("previous_version_id") or "")
    if not version:
        raise CloudflareDeployError("receipt has no previous version to roll back to")
    env_map = dict(os.environ if env is None else env)
    identity, secrets = online_identity(project, contract, runner=runner, env=env_map)
    validate_contract(
        project, contract, env=env_map, identity_output=identity,
        remote_secret_names=secrets,
    )
    result = _run_checked(
        runner,
        _wrangler_base(contract) + [
            "rollback", version, "--name", str(manifest["worker_name"]),
            "--message", f"hermes rollback from {manifest['source_commit'][:12]}",
        ],
        project,
        env_map,
        300,
    )
    rollback = {
        "ok": True, "rolled_back_to": version,
        "output_summary": (result.stdout + result.stderr)[-2000:],
    }
    path = manifest_path.resolve().parent / "rollback-receipt.json"
    path.write_text(json.dumps(rollback, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rollback["receipt"] = str(path)
    return rollback


def _resolve_manifest(args: Any, artifacts_dir: Path) -> Path:
    if getattr(args, "manifest", None):
        return Path(args.manifest).expanduser().resolve()
    manifests = sorted((artifacts_dir / "artifacts").glob("*/manifest.json"), reverse=True)
    if not manifests:
        raise CloudflareDeployError("no artifact manifest found; run prepare first")
    return manifests[0]


def cloudflare_deploy_command(args: Any) -> int:
    project = Path(args.project).expanduser().resolve()
    contract_path = Path(args.contract).expanduser()
    if not contract_path.is_absolute():
        contract_path = project / contract_path
    artifacts_dir = (
        Path(args.artifacts_dir).expanduser().resolve()
        if args.artifacts_dir else project / ".hermes-deploy"
    )
    try:
        contract = load_contract(contract_path)
        if args.action == "validate":
            env_map = dict(os.environ)
            identity, secrets = online_identity(
                project, contract, runner=_default_runner, env=env_map
            )
            result: Any = validate_contract(
                project,
                contract,
                env=env_map,
                identity_output=identity,
                remote_secret_names=secrets,
            )
            result["identity_verified"] = True
        elif args.action == "prepare":
            manifest = build_artifact(
                project, contract, artifacts_dir=artifacts_dir,
                allow_dirty=bool(args.allow_dirty),
            )
            result = {"ok": True, "manifest": str(manifest)}
        elif args.action == "deploy":
            if not args.confirm_upload:
                raise CloudflareDeployError("external upload requires --confirm-upload")
            manifest = build_artifact(
                project, contract, artifacts_dir=artifacts_dir,
                allow_dirty=bool(args.allow_dirty),
            )
            receipt = deploy_manifest(
                manifest, project, contract, confirm_upload=True
            )
            smoke = smoke_manifest(manifest, contract)
            result = {
                "ok": True, "manifest": str(manifest),
                "receipt": str(receipt), "smoke": smoke,
            }
        elif args.action == "smoke":
            manifest = _resolve_manifest(args, artifacts_dir)
            result = smoke_manifest(manifest, contract)
        else:
            manifest = _resolve_manifest(args, artifacts_dir)
            result = rollback_manifest(
                manifest, project, contract,
                confirm_rollback=bool(args.confirm_rollback),
            )
    except CloudflareDeployError as exc:
        result = {"ok": False, "error": str(exc)}
        print(
            json.dumps(result, ensure_ascii=False)
            if args.json else f"Cloudflare deploy blocked: {exc}"
        )
        return 2
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print("Cloudflare deployment contract: PASS")
        if isinstance(result, dict):
            for key in ("manifest", "receipt", "base_url", "artifact_digest", "source_commit"):
                if result.get(key):
                    print(f"  {key}: {result[key]}")
    return 0


__all__ = [
    "CloudflareDeployError", "CommandResult", "build_artifact",
    "cloudflare_deploy_command", "deploy_manifest", "load_and_verify_manifest",
    "load_contract", "load_wrangler_config", "rollback_manifest",
    "smoke_manifest", "validate_contract",
]
