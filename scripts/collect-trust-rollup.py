#!/usr/bin/env python3
"""Roll up trust scorecards into a per-agent CSV for fullsend-ai.

Reads a JSON array of trust scorecards (the artifact shape proposed in
fullsend-ai/fullsend docs/problems/trustworthiness-evidence.md) and writes
docs/trust-rollup.csv, one row per (repo, agent_role): how many scorecards
were seen, the most recent composition decision, the share of evidence
signals that passed, the average of the numeric evidence scores, and the
union of signals that ever blocked a decision.

No live trust-evidence source exists yet, so this reads a documented sample
fixture (docs/trust-evidence.sample.json) by default. Point --input at a real
feed once one exists; the rollup logic is unchanged.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_FILE = ROOT / "docs" / "trust-evidence.sample.json"
ROLLUP_FILE = ROOT / "docs" / "trust-rollup.csv"

ROLLUP_HEADER = [
    "repo", "agent_role", "scorecards", "latest_decision",
    "signal_pass_rate", "avg_config_health", "avg_behavioral_eval",
    "avg_track_record_revert_rate", "blocking_signals",
]

# Evidence signals expected on a scorecard; unknown keys are tolerated.
SIGNALS = ("config_health", "behavioral_eval", "audit_integrity",
           "track_record", "drift")


def load_scorecards(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        # Only unwrap the documented {"scorecards": [...]} envelope. A dict
        # without that key is an unexpected shape, not an empty feed — reject
        # it loudly rather than silently rolling up zero scorecards.
        if "scorecards" not in data:
            raise ValueError(
                f"{path}: object is missing the 'scorecards' key; "
                "expected a JSON array or {\"scorecards\": [...]}"
            )
        data = data["scorecards"]
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON array of scorecards")
    return data


def _instant(card: dict) -> datetime:
    """Parse a scorecard's generated_at into a UTC-comparable instant.

    ISO-8601 strings with different UTC offsets do not sort correctly as raw
    text (e.g. '...T10:00+02:00' is earlier than '...T09:00Z' but sorts after
    it), so compare parsed instants instead. Unparseable or missing timestamps
    sort oldest so they never spuriously win "latest".
    """
    raw = (card.get("subject") or {}).get("generated_at")
    if not isinstance(raw, str):
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        # fromisoformat accepts a trailing 'Z' only on 3.11+; normalize it.
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    # Treat naive timestamps as UTC so they compare against aware ones.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _mean(values):
    vals = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return round(sum(vals) / len(vals), 4) if vals else ""


def rollup(scorecards: list[dict]) -> list[dict]:
    """Aggregate scorecards into one row per (repo, agent_role)."""
    groups = defaultdict(list)
    for card in scorecards:
        subj = card.get("subject") or {}
        key = (subj.get("repo") or "", subj.get("agent_role") or "")
        groups[key].append(card)

    rows = []
    for (repo, role), cards in sorted(groups.items()):
        # Most recent by generated_at, compared as parsed UTC instants.
        latest = max(cards, key=_instant)
        passed = total = 0
        config_scores, behav_scores, revert_rates = [], [], []
        blocking = set()
        for card in cards:
            evidence = card.get("evidence") or {}
            for name in SIGNALS:
                sig = evidence.get(name)
                if not isinstance(sig, dict):
                    continue
                total += 1
                if sig.get("status") == "pass":
                    passed += 1
            config_scores.append((evidence.get("config_health") or {}).get("score"))
            behav_scores.append((evidence.get("behavioral_eval") or {}).get("score"))
            revert_rates.append((evidence.get("track_record") or {}).get("revert_rate"))
            blocking.update((card.get("composition") or {}).get("blocking") or [])
        rows.append({
            "repo": repo,
            "agent_role": role,
            "scorecards": len(cards),
            "latest_decision": (latest.get("composition") or {}).get("decision") or "",
            "signal_pass_rate": round(passed / total, 4) if total else "",
            "avg_config_health": _mean(config_scores),
            "avg_behavioral_eval": _mean(behav_scores),
            "avg_track_record_revert_rate": _mean(revert_rates),
            "blocking_signals": ";".join(sorted(blocking)),
        })
    return rows


def write_rollup(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ROLLUP_HEADER)
        w.writeheader()
        w.writerows(rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", default=str(SAMPLE_FILE),
                   help="Trust scorecard JSON (default: docs/trust-evidence.sample.json)")
    p.add_argument("--output", default=str(ROLLUP_FILE),
                   help="CSV to write (default: docs/trust-rollup.csv)")
    args = p.parse_args()

    scorecards = load_scorecards(Path(args.input))
    rows = rollup(scorecards)
    write_rollup(rows, Path(args.output))
    print(f"Wrote {len(rows)} rollup rows from {len(scorecards)} scorecards to {args.output}")


if __name__ == "__main__":
    main()
