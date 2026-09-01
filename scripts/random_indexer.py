#!/usr/bin/env python3
"""
Random Block Indexer for $U Transfers on BSC
=============================================
Selects random blocks from a range and indexes them via the production API.
Tracks used blocks in SQLite to avoid re-processing.

Usage:
    python3 scripts/random_indexer.py --from 72122100 --to 72500000 --count 100
    python3 scripts/random_indexer.py --from 72122100 --to 72500000 --count 50 --delay 2
    python3 scripts/random_indexer.py --show
    python3 scripts/random_indexer.py --reset
"""

import argparse
import json
import os
import random
import sqlite3
import sys
import time
from datetime import datetime

try:
    import requests
except ImportError:
    print("❌ Necesitás requests: pip install requests")
    sys.exit(1)


# ─── Config ───────────────────────────────────────────────────────────
API_BASE = "http://proyecto-atlas-bnbmarket-sbqbwu-b70a44-194-163-177-206.sslip.io"
DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "indexer_used_blocks.db")
DEFAULT_DELAY = 1.0
DEFAULT_COUNT = 50
TIMEOUT = 30


# ─── Colors ───────────────────────────────────────────────────────────
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"


# ─── Database ─────────────────────────────────────────────────────────
def init_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS used_blocks (
            block_number INTEGER PRIMARY KEY,
            status TEXT DEFAULT 'used',
            transfers INTEGER DEFAULT 0,
            events INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def get_used_blocks(conn):
    rows = conn.execute("SELECT block_number FROM used_blocks").fetchall()
    return set(r[0] for r in rows)


def mark_used(conn, block, transfers=0, events=0, status="used"):
    conn.execute("""
        INSERT OR REPLACE INTO used_blocks (block_number, status, transfers, events, created_at)
        VALUES (?, ?, ?, ?, datetime('now'))
    """, (block, status, transfers, events))
    conn.commit()


def show_stats(conn):
    total = conn.execute("SELECT COUNT(*) FROM used_blocks").fetchone()[0]
    ok = conn.execute("SELECT COUNT(*) FROM used_blocks WHERE status='used'").fetchone()[0]
    err = conn.execute("SELECT COUNT(*) FROM used_blocks WHERE status='error'").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM used_blocks WHERE status='pending'").fetchone()[0]

    total_transfers = conn.execute("SELECT COALESCE(SUM(transfers), 0) FROM used_blocks").fetchone()[0]
    total_events = conn.execute("SELECT COALESCE(SUM(events), 0) FROM used_blocks").fetchone()[0]

    first = conn.execute("SELECT MIN(block_number) FROM used_blocks").fetchone()[0]
    last = conn.execute("SELECT MAX(block_number) FROM used_blocks").fetchone()[0]

    print(f"\n{C.BOLD}📊 Indexer Stats{C.RESET}")
    print(f"{'─' * 40}")
    print(f"  Blocks indexed:  {C.GREEN}{ok}{C.RESET} ok  |  {C.RED}{err}{C.RESET} error  |  {C.YELLOW}{pending}{C.RESET} pending  |  {total} total")
    print(f"  Transfers $U:    {C.CYAN}{total_transfers}{C.RESET}")
    print(f"  Agent events:    {C.CYAN}{total_events}{C.RESET}")
    if first and last:
        print(f"  Range covered:   {first} → {last}")
    print(f"  DB: {C.DIM}{DB_PATH}{C.RESET}\n")


def reset_db(conn):
    conn.execute("DELETE FROM used_blocks")
    conn.commit()
    print(f"{C.GREEN}✅ Database reset.{C.RESET}")


# ─── API ──────────────────────────────────────────────────────────────
def index_block(block):
    """Call GET /api/onchain/index/{block} and return the response."""
    url = f"{API_BASE}/api/onchain/index/{block}"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        data = r.json()
        return data
    except requests.exceptions.Timeout:
        return {"error": "timeout"}
    except requests.exceptions.ConnectionError:
        return {"error": "connection_error"}
    except Exception as e:
        return {"error": str(e)}


# ─── Main ─────────────────────────────────────────────────────────────
def pick_random_blocks(from_block, to_block, count, used):
    """Pick `count` random blocks from range that haven't been used."""
    available = [b for b in range(from_block, to_block + 1) if b not in used]
    if not available:
        return []
    pick = min(count, len(available))
    return random.sample(available, pick)


def run(args):
    conn = init_db()
    used = get_used_blocks(conn)

    total_in_range = args.to_block - args.from_block + 1
    available = total_in_range - len(used & set(range(args.from_block, args.to_block + 1)))

    print(f"\n{C.BOLD}🎲 Random Block Indexer{C.RESET}")
    print(f"{'─' * 40}")
    print(f"  Range:    {args.from_block} → {args.to_block} ({total_in_range:,} blocks)")
    print(f"  Used:     {len(used):,}")
    print(f"  Available:{C.GREEN} {available:,}{C.RESET}")
    print(f"  Pick:     {args.count}")
    print(f"  Delay:    {args.delay}s between requests")
    print(f"  API:      {C.DIM}{API_BASE}{C.RESET}")
    print()

    if available == 0:
        print(f"{C.YELLOW}⚠️  Todos los bloques del rango ya fueron indexados.{C.RESET}")
        show_stats(conn)
        return

    blocks = pick_random_blocks(args.from_block, args.to_block, args.count, used)
    if not blocks:
        print(f"{C.YELLOW}⚠️  No se pudieron seleccionar bloques.{C.RESET}")
        return

    print(f"{C.CYAN}📌 Bloques seleccionados: {len(blocks)}{C.RESET}")
    print(f"   Primeros 10: {', '.join(str(b) for b in sorted(blocks)[:10])}{'...' if len(blocks) > 10 else ''}")
    print()

    # Mark as pending
    for b in blocks:
        mark_used(conn, b, status="pending")

    # Index each block
    ok_count = 0
    err_count = 0
    total_transfers = 0
    total_events = 0

    for i, block in enumerate(sorted(blocks), 1):
        progress = f"[{i}/{len(blocks)}]"
        print(f"{C.DIM}{progress}{C.RESET} Block {C.BOLD}{block}{C.RESET} ... ", end="", flush=True)

        data = index_block(block)

        if "error" in data:
            err_count += 1
            mark_used(conn, block, status="error")
            print(f"{C.RED}❌ {data['error']}{C.RESET}")
        else:
            t = data.get("transfers", 0)
            e = data.get("events", 0)
            total_transfers += t
            total_events += e
            ok_count += 1
            mark_used(conn, block, transfers=t, events=e)

            detail = []
            if t > 0:
                detail.append(f"{C.CYAN}💰 {t} transfers{C.RESET}")
            if e > 0:
                detail.append(f"{C.CYAN}🎫 {e} events{C.RESET}")
            if not detail:
                detail.append(f"{C.DIM}vacío{C.RESET}")

            print(f"{C.GREEN}✅{C.RESET} {' | '.join(detail)}")

        # Delay between requests (skip on last)
        if i < len(blocks):
            time.sleep(args.delay)

    # Final summary
    print(f"\n{'─' * 40}")
    print(f"{C.BOLD}📊 Resultado:{C.RESET}")
    print(f"  ✅ OK:        {C.GREEN}{ok_count}{C.RESET}")
    print(f"  ❌ Errores:   {C.RED}{err_count}{C.RESET}")
    print(f"  💰 Transfers: {C.CYAN}{total_transfers}{C.RESET}")
    print(f"  🎫 Events:    {C.CYAN}{total_events}{C.RESET}")

    if err_count > 0:
        print(f"\n  {C.YELLOW}Tip: los bloques con error se marcaron como 'error'.{C.RESET}")
        print(f"  {C.YELLOW}Los podés reintentar borrandolos de la DB o con --reset.{C.RESET}")

    print()
    show_stats(conn)
    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Randomly index BSC blocks for $U transfers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --from 72122100 --to 72500000 --count 100
  %(prog)s --from 72122100 --to 72500000 --count 50 --delay 2
  %(prog)s --show
  %(prog)s --reset
        """,
    )
    parser.add_argument("--from", dest="from_block", type=int, help="First block in range")
    parser.add_argument("--to", dest="to_block", type=int, help="Last block in range")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help=f"Blocks to pick (default: {DEFAULT_COUNT})")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help=f"Seconds between requests (default: {DEFAULT_DELAY})")
    parser.add_argument("--show", action="store_true", help="Show stats and exit")
    parser.add_argument("--reset", action="store_true", help="Reset used blocks DB and exit")

    args = parser.parse_args()

    conn = init_db()

    if args.show:
        show_stats(conn)
        conn.close()
        return

    if args.reset:
        reset_db(conn)
        conn.close()
        return

    if not args.from_block or not args.to_block:
        print(f"{C.RED}❌ Necesitás --from y --to{C.RESET}")
        parser.print_help()
        conn.close()
        sys.exit(1)

    if args.from_block >= args.to_block:
        print(f"{C.RED}❌ --from debe ser menor que --to{C.RESET}")
        conn.close()
        sys.exit(1)

    try:
        run(args)
    except KeyboardInterrupt:
        print(f"\n\n{C.YELLOW}⏹  Detenido por el usuario.{C.RESET}")
        show_stats(conn)
        conn.close()


if __name__ == "__main__":
    main()
