"""``hermes deploy`` parser."""

from __future__ import annotations

import argparse
from typing import Callable


def build_deploy_parser(subparsers, *, cmd_deploy: Callable) -> None:
    parser = subparsers.add_parser(
        "deploy",
        help="Build and deploy reproducible provider artifacts",
        description="Provider workflows with immutable manifests and contractual smoke tests.",
    )
    providers = parser.add_subparsers(dest="deploy_provider", required=True)
    cloudflare = providers.add_parser(
        "cloudflare",
        help="Atomic Next.js/OpenNext deployment to Cloudflare Workers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  hermes deploy cloudflare prepare --project .
  hermes deploy cloudflare deploy --project . --confirm-upload
  hermes deploy cloudflare smoke --project . --manifest PATH
  hermes deploy cloudflare rollback --project . --manifest PATH --confirm-rollback
""",
    )
    cloudflare.add_argument(
        "action", choices=("validate", "prepare", "deploy", "smoke", "rollback")
    )
    cloudflare.add_argument("--project", default=".", help="Next.js project root")
    cloudflare.add_argument(
        "--contract", default="cloudflare.deploy.yaml", help="Deployment contract path"
    )
    cloudflare.add_argument("--manifest", help="Existing artifact manifest")
    cloudflare.add_argument(
        "--artifacts-dir", help="Artifact store (default: project/.hermes-deploy)"
    )
    cloudflare.add_argument(
        "--allow-dirty", action="store_true", help="Allow a dirty git source tree"
    )
    cloudflare.add_argument(
        "--confirm-upload", action="store_true", help="Confirm the external upload"
    )
    cloudflare.add_argument(
        "--confirm-rollback", action="store_true",
        help="Confirm immediate rollback traffic change",
    )
    cloudflare.add_argument("--json", action="store_true", help="Print JSON")
    cloudflare.set_defaults(func=cmd_deploy)
