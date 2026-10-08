"""Scriptable companion to the local UI."""
import argparse
import json
import os
from pathlib import Path

from .provider import DEFAULT_MODEL, ProviderError
from .pipeline import qualify_one
from .workspace import Workspace, parse_csv, export_csv

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="SignalDesk: reusable lead qualification")
    parser.add_argument("--db", default=str(ROOT / ".data" / "workspace.db"))
    commands = parser.add_subparsers(dest="command", required=True)
    configure = commands.add_parser("configure", help="Import your product profile JSON")
    configure.add_argument("file", type=Path)
    import_cmd = commands.add_parser("import", help="Import a UTF-8 lead CSV")
    import_cmd.add_argument("file", type=Path)
    commands.add_parser("list", help="List lead IDs and saved routes")
    run = commands.add_parser("run", help="Qualify leads using OPENAI_API_KEY")
    choose = run.add_mutually_exclusive_group(required=True)
    choose.add_argument("--lead", action="append", help="Lead ID; repeat for multiple leads")
    choose.add_argument("--pending", action="store_true", help="Process unqualified leads, up to --limit")
    run.add_argument("--limit", type=int, default=10)
    run.add_argument("--model", default=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL))
    run.add_argument("--inquiry-only", action="store_true")
    run.add_argument("--allow-provider", action="store_true", help="Authorize transmitting selected inquiries and website evidence to OpenAI; charges apply")
    export = commands.add_parser("export", help="Export the current queue as CSV")
    export.add_argument("output", type=Path)
    export.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    store = Workspace(args.db)
    try:
        if args.command == "configure":
            profile = store.save_profile(json.loads(args.file.read_text()))
            print("Saved {} (profile {}).".format(profile["name"], profile["version"]))
        elif args.command == "import":
            if args.file.stat().st_size > 1500000:
                raise ValueError("CSV must be smaller than 1.5 MB.")
            result = store.import_rows(parse_csv(args.file.read_text(encoding="utf-8-sig")))
            print("Imported {}; skipped {} duplicates.".format(len(result["added"]),result["skipped"]))
        elif args.command == "list":
            for lead in store.leads():
                print("{}  {}  {}".format(lead["id"],lead["company"],lead["route"] or "Not qualified"))
        elif args.command == "export":
            if args.output.exists() and not args.overwrite:
                raise ValueError("Output exists. Use a new filename or --overwrite.")
            args.output.write_text(export_csv(store.leads()),encoding="utf-8")
            print("Exported to " + str(args.output))
        elif args.command == "run":
            if not args.allow_provider:
                raise ValueError("Use --allow-provider to authorize sending these inputs to OpenAI using your API account.")
            if not 1 <= args.limit <= 20:
                raise ValueError("Use a limit between 1 and 20.")
            if not os.environ.get("OPENAI_API_KEY"):
                raise ValueError("Set OPENAI_API_KEY in the environment. Never commit an API key.")
            profile = store.setting("profile")
            if not profile:
                raise ValueError("Configure a product profile first.")
            ids = args.lead or [l["id"] for l in store.leads() if not l["latest_run"]][:args.limit]
            if len(ids) > 20 or len(ids) != len(set(ids)):
                raise ValueError("Specify at most 20 unique lead IDs.")
            failures = 0
            for lead_id in ids:
                try:
                    result = qualify_one(store,lead_id,profile,os.environ["OPENAI_API_KEY"],args.model,not args.inquiry_only)
                    print("{}: {}".format(lead_id,result["result"]["route"]))
                except (ValueError,KeyError) as error:
                    failures += 1
                    print("{}: failed — {}".format(lead_id,error))
                    if isinstance(error, ProviderError) and error.stop_batch:
                        print("Remaining leads skipped. Resolve the provider connection or quota error before retrying.")
                        break
            if failures:
                raise SystemExit(1)
            if not ids:
                print("No pending leads.")
    except (ValueError,KeyError,OSError) as error:
        parser.exit(1,"Error: {}\n".format(error))


if __name__ == "__main__":
    main()
