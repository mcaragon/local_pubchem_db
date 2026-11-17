"""
Command line interface:

    pubchem-local serve                     start the web UI / API server
    pubchem-local build --datasets lcss     download and build databases (lcss, names, compounds or all)
    pubchem-local status                    show what has been built
"""
import argparse
import signal
import sys

from pubchem_local import config


def _raise_keyboard_interrupt(signum, frame):
    raise KeyboardInterrupt()


def cmd_build(args):
    from pubchem_local import pipeline
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    datasets = config.DATASETS if args.datasets in ("all", "full") else [d.strip() for d in args.datasets.split(",")]
    unknown = [d for d in datasets if d not in config.DATASETS]
    if unknown:
        print("Unknown dataset(s): %s (choose from %s)" % (", ".join(unknown), ", ".join(config.DATASETS)))
        return 2
    return pipeline.run(datasets, fresh=args.fresh)


def cmd_status(args):
    from pubchem_local import pipeline, jobs
    import json
    print(pipeline.describe())
    status = jobs.current_status()
    status.pop("log", None)
    print(json.dumps(status, indent=1))
    return 0


def cmd_serve(args):
    import uvicorn
    from pubchem_local import jobs, pipeline
    config.ensure_dirs()
    jobs.current_status()  # mark builds interrupted by a restart
    auto = [d for d in config.auto_build() if not config.db_path(d).exists()]
    if auto and not jobs.is_running():
        print("AUTO_BUILD: starting build of %s" % ", ".join(auto))
        jobs.start_build(auto)
    uvicorn.run("pubchem_local.api:app", host=args.host, port=args.port, log_level="info",
                proxy_headers=True, forwarded_allow_ips="*")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pubchem-local", description="Self-hosted offline PubChem")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("serve", help="Start the web UI and REST API")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=int(config._env("PORT", 8080)))
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("build", help="Download PubChem files and build the databases")
    p.add_argument("--datasets", default="lcss,names",
                   help="Comma separated list of: lcss, names, compounds (or 'all'). Default: lcss,names")
    p.add_argument("--fresh", action="store_true", help="Discard a partially built compounds database")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("status", help="Show the state of the databases and the last build")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
