import os
import time
import csv
import requests
from datetime import datetime, timezone

# === CONFIG ===
MINT = "YOUR_SOL_TOKEN_HERE"

# Window ex: 26/11 03:00 -> 06:00 Paris (UTC+1) == 02:00Z -> 05:00Z
START_TS = 1760806800
END_TS = 1760864400

TOP_POOLS = 5          # how many top Dexscreener pairs to scan
SIG_PAGE_LIMIT = 1000  # max signatures per RPC page
PARSE_BATCH = 100      # max signatures per /v0/transactions call
SLEEP_S = 0.12         # rate-limit friendliness for free tier

DEXSCREENER_TOKEN_PAIRS = "https://api.dexscreener.com/tokens/v1/solana/{token}"
HELIUS_RPC_URL = "https://mainnet.helius-rpc.com/?api-key={api_key}"
HELIUS_PARSE_URL = "https://api-mainnet.helius-rpc.com/v0/transactions?api-key={api_key}"


def http_get(url, params=None, timeout=30):
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def http_post(url, payload, timeout=60):
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_top_pairs_from_dexscreener(mint: str, top_n: int):
    data = http_get(DEXSCREENER_TOKEN_PAIRS.format(token=mint))

    def score(p):
        liq = (p.get("liquidity") or {}).get("usd") or 0
        vol = (p.get("volume") or {}).get("h24") or 0
        return (liq * 10) + vol

    pairs = sorted(data, key=score, reverse=True)
    out = []
    for p in pairs:
        base = (p.get("baseToken") or {}).get("address")
        quote = (p.get("quoteToken") or {}).get("address")
        # keep only pairs that actually include our mint
        if base == mint or quote == mint:
            out.append(p)
        if len(out) >= top_n:
            break
    return out


def rpc(api_key: str, method: str, params):
    url = HELIUS_RPC_URL.format(api_key=api_key)
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    res = http_post(url, payload)
    if "error" in res:
        raise RuntimeError(res["error"])
    return res["result"]


def fetch_signatures_in_window(api_key: str, address: str, start_ts: int, end_ts: int):
    # Newest -> oldest, stop once blockTime < start_ts
    sigs = []
    before = None
    pages = 0

    while True:
        cfg = {"limit": SIG_PAGE_LIMIT}
        if before:
            cfg["before"] = before

        batch = rpc(api_key, "getSignaturesForAddress", [address, cfg])
        pages += 1
        if not batch:
            break

        bt_new = batch[0].get("blockTime")
        bt_old = batch[-1].get("blockTime")
        print(f"  sig page {pages}: {len(batch)} sigs (blockTime newest={bt_new}, oldest={bt_old})")

        for item in batch:
            bt = item.get("blockTime")
            if bt is None:
                continue
            if bt < start_ts:
                return sigs
            if start_ts <= bt <= end_ts:
                sigs.append(item["signature"])

        before = batch[-1]["signature"]
        time.sleep(SLEEP_S)

    return sigs


def extract_buyers_from_parsed_tx(tx: dict, mint: str):
    """
    Returns list of tuples: (buyer_wallet, raw_amount_str_or_none, decimals_or_none, signature, timestamp)
    Strategy:
      1) swap tokenOutputs (best)
      2) tokenTransfers fallback
      3) feePayer fallback if buyer fields absent
    """
    out = []
    sig = tx.get("signature")
    ts = tx.get("timestamp")
    fee_payer = tx.get("feePayer")

    # 1) swap outputs
    swap = (tx.get("events") or {}).get("swap") or {}
    outputs = list(swap.get("tokenOutputs") or [])
    for s in (swap.get("innerSwaps") or []):
        outputs += (s.get("tokenOutputs") or [])

    for o in outputs:
        if o.get("mint") != mint:
            continue
        buyer = o.get("toUserAccount") or o.get("userAccount") or fee_payer
        rta = o.get("rawTokenAmount") or {}
        raw_amt = rta.get("tokenAmount")
        dec = rta.get("decimals")
        if buyer:
            out.append((buyer, raw_amt, dec, sig, ts))

    # 2) tokenTransfers fallback
    if not out:
        for t in (tx.get("tokenTransfers") or []):
            if t.get("mint") != mint:
                continue
            buyer = t.get("toUserAccount") or fee_payer
            raw_amt = t.get("tokenAmount")
            dec = t.get("decimals")
            if buyer:
                out.append((buyer, str(raw_amt) if raw_amt is not None else None, dec, sig, ts))

    return out


def main():
    api_key = os.getenv("HELIUS_KEY")
    if not api_key:
        raise SystemExit('Set it in PowerShell:  $env:HELIUS_KEY="YOUR_API_KEY"')

    print(
        "UTC window:",
        datetime.fromtimestamp(START_TS, tz=timezone.utc).isoformat(),
        "->",
        datetime.fromtimestamp(END_TS, tz=timezone.utc).isoformat(),
    )

    pairs = get_top_pairs_from_dexscreener(MINT, top_n=TOP_POOLS)
    if not pairs:
        raise SystemExit("No Dexscreener pairs found that include this mint. (Wrong mint or not traded yet.)")

    buyers_agg = {}

    for p in pairs:
        pair_addr = p.get("pairAddress")
        if not pair_addr:
            continue

        base = (p.get("baseToken") or {}).get("address")
        quote = (p.get("quoteToken") or {}).get("address")

        print(f"\nPool: {pair_addr} (dexId={p.get('dexId')})")
        print(f"  base:  {base}")
        print(f"  quote: {quote}")
        print(f"  (your mint): {MINT}")

        # Extra safety: skip if somehow not our mint (shouldn't happen due to filtering)
        if base != MINT and quote != MINT:
            print("  !! Skipping (pair does not include your mint)")
            continue

        sigs = fetch_signatures_in_window(api_key, pair_addr, START_TS, END_TS)
        sigs = list(dict.fromkeys(sigs))
        print(f"  signatures in window: {len(sigs)}")

        seen_mint_transfers = 0
        parse_url = HELIUS_PARSE_URL.format(api_key=api_key)

        for i in range(0, len(sigs), PARSE_BATCH):
            chunk = sigs[i : i + PARSE_BATCH]
            parsed = http_post(parse_url, {"transactions": chunk})

            for tx in parsed:
                t = tx.get("timestamp")
                if t is None or not (START_TS <= t <= END_TS):
                    continue

                # sanity: did your mint appear at all?
                if any((tr.get("mint") == MINT) for tr in (tx.get("tokenTransfers") or [])):
                    seen_mint_transfers += 1

                for (wallet, amt, dec, sig, ts) in extract_buyers_from_parsed_tx(tx, MINT):
                    e = buyers_agg.get(wallet)
                    raw_int = int(amt) if isinstance(amt, str) and amt.isdigit() else 0

                    if not e:
                        buyers_agg[wallet] = {
                            "wallet": wallet,
                            "first_ts": ts,
                            "last_ts": ts,
                            "tx_count": 1,
                            "raw_total": raw_int,
                            "decimals": dec,
                            "example_sig": sig,
                        }
                    else:
                        e["first_ts"] = min(e["first_ts"], ts)
                        e["last_ts"] = max(e["last_ts"], ts)
                        e["tx_count"] += 1
                        e["raw_total"] += raw_int
                        if e.get("decimals") is None and dec is not None:
                            e["decimals"] = dec

            print(f"  parsed {min(i + PARSE_BATCH, len(sigs))}/{len(sigs)}")
            time.sleep(SLEEP_S)

        print(f"  txs in window where mint appeared in tokenTransfers: {seen_mint_transfers}")

    rows = sorted(buyers_agg.values(), key=lambda x: x["first_ts"])
    out_file = f"buyers_{MINT[:6]}_{START_TS}_{END_TS}.csv"

    with open(out_file, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["wallet", "first_seen_utc", "last_seen_utc", "tx_count", "raw_total", "decimals", "example_signature"])
        for r in rows:
            w.writerow(
                [
                    r["wallet"],
                    datetime.fromtimestamp(r["first_ts"], tz=timezone.utc).isoformat(),
                    datetime.fromtimestamp(r["last_ts"], tz=timezone.utc).isoformat(),
                    r["tx_count"],
                    r["raw_total"],
                    r["decimals"],
                    r["example_sig"],
                ]
            )

    print(f"\nUnique buyers: {len(rows)}")
    print("Wrote:", out_file)


if __name__ == "__main__":
    main()
