"""CLI: python -m nse_agent <run | once | check | test-email | fno [SYMBOL]> [--dry-run] [--env-file PATH]"""
import argparse
import logging
import sys
import time
from datetime import datetime

from . import clock
from .config import Config
from .engine import Agent
from .mcp import McpClient, McpError
from .notify import Mailer
from .nse import NseData
from .state import State


def build(cfg: Config) -> Agent:
    data = NseData(McpClient(cfg.cm_url), McpClient(cfg.bhav_url))
    return Agent(cfg, data, Mailer(cfg), State(cfg.state_dir))


def sleep_seconds(now: datetime) -> int:
    """60 s while the desk is active, longer overnight and on weekends."""
    if clock.phase(now) == "weekend":
        return 900
    return 60 if 8 <= now.hour < 17 else 600


def run_forever(agent: Agent) -> None:
    log = logging.getLogger("nse_agent")
    log.info("agent started; email to %s; dry_run=%s", agent.cfg.email_to or "(not set)", agent.cfg.dry_run)
    while True:
        now = clock.now_ist()
        try:
            result = agent.tick(now)
            if not result.startswith("idle"):
                log.info("tick: %s", result)
        except Exception:  # keep the loop alive through any single failure
            log.exception("tick failed")
        time.sleep(sleep_seconds(now))


def check(agent: Agent) -> int:
    ok = True
    try:
        n = agent.data.index_quote("NIFTY 50")
        print(f"CM Live OK: NIFTY 50 {n.last:,.2f} ({n.pct:+.2f}%), NSE timestamp {n.ts}")
        print("  live right now" if agent.live_index(clock.now_ist()) else "  not live now (closed, holiday or stale)")
    except (McpError, KeyError) as exc:
        ok = False
        print(f"CM Live FAILED: {exc}")
    try:
        b = agent.data.breadth(clock.now_ist().date())
        print(f"Bhavcopy OK: breadth {b.get('date')} A/D {b.get('ad_ratio')}")
    except (McpError, KeyError) as exc:
        ok = False
        print(f"Bhavcopy FAILED: {exc}")
    print(f"Email configured: {agent.cfg.can_email()} (to {agent.cfg.email_to or 'unset'}); "
          f"LLM: {agent.cfg.llm_provider}")
    return 0 if ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="nse_agent")
    p.add_argument("command", choices=["run", "once", "check", "test-email", "fno"])
    p.add_argument("symbol", nargs="?", default="NIFTY", help="underlying for the fno command")
    p.add_argument("--dry-run", action="store_true", help="print emails instead of sending")
    p.add_argument("--env-file")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.from_env(args.env_file)
    cfg.dry_run = cfg.dry_run or args.dry_run
    if cfg.dry_run:
        cfg.state_dir += "_dry"  # keep rehearsals out of the real state
    agent = build(cfg)
    if args.command == "check":
        return check(agent)
    if args.command == "test-email":
        sent = agent.mailer.send("[NSE Desk] Test email", "If you can read this, Gmail delivery works.")
        print("sent" if sent else "NOT sent: set SMTP_USER, SMTP_APP_PASSWORD and EMAIL_TO in .env")
        return 0 if sent else 1
    if args.command == "fno":
        from .fno_render import render_view
        try:
            print(render_view(agent.fno_view(args.symbol, clock.now_ist()), clock.now_ist()))
        except Exception as exc:  # noqa: BLE001  a CLI should print the reason, not a traceback
            print(f"F&O view failed: {exc}")
            return 1
        return 0
    if args.command == "once":
        print(agent.tick())
        return 0
    run_forever(agent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
