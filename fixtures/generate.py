"""
Deterministic synthetic mailbox fixture generator (S0 prerequisite).

No RNG — the scenario is fully specified, so output is byte-identical every run.
Run:  python fixtures/generate.py   →   writes mailbox.json + gold/*.json next to this file.

This single labeled mailbox feeds the acceptance gates across layers:
  - spec 00 §19  thread reconstruction (incl. subject-reuse no-false-merge, missing Message-ID),
                 quote/signature stripping, noise + sensitivity classification.
  - spec 01 §3   identity resolution (one person / two addresses; a name collision that must NOT merge).
  - spec 01 §5   role inference (internal / manager / AE / lead / vendor).
  - spec 03 §18  project clustering (thread→project gold, with one multi-project thread).
Gold labels live alongside each message in SCENARIO and are split out into gold/*.json on emit.
"""
import json, hashlib
from pathlib import Path

OUT = Path(__file__).parent
OWNER_EMAIL = "alex@acme.com"
INTERNAL_DOMAINS = ["acme.com"]

# person_id -> identity + gold role. Two addresses for p_jenna (merge target);
# p_jbrooks shares the first name "Jenna" but is a different person (must NOT merge).
PEOPLE = {
    "p_alex":   {"name": "Alex Rivera",  "addresses": ["alex@acme.com"],        "role": "internal", "org": "acme.com"},
    "p_jenna":  {"name": "Jenna Park",   "addresses": ["jenna@acme.com", "j.park@acme.com"], "role": "internal", "org": "acme.com"},
    "p_raj":    {"name": "Raj Patel",    "addresses": ["raj@acme.com"],         "role": "internal", "org": "acme.com"},
    "p_aiko":   {"name": "Aiko Tanaka",  "addresses": ["aiko@acme.com"],        "role": "internal", "org": "acme.com"},
    "p_grace":  {"name": "Grace Mueller","addresses": ["grace@acme.com"],       "role": "manager",  "org": "acme.com"},
    "p_dana":   {"name": "Dana Okafor",  "addresses": ["dana@northwind.com"],   "role": "account_exec", "org": "northwind.com"},
    "p_marcus": {"name": "Marcus Lee",   "addresses": ["marcus@cloudpeak.io"],  "role": "lead",     "org": "cloudpeak.io"},
    "p_ben":    {"name": "Ben Carter",   "addresses": ["ben@datapipe.com"],     "role": "vendor",   "org": "datapipe.com"},
    "p_jbrooks":{"name": "Jenna Brooks", "addresses": ["jenna@vertexlabs.com"], "role": "lead",     "org": "vertexlabs.com"},
    "p_hr":     {"name": "People Ops",   "addresses": ["hr@acme.com"],          "role": "internal", "org": "acme.com"},
    "p_legal":  {"name": "Morris Law",   "addresses": ["counsel@morrislaw.com"],"role": "vendor",   "org": "morrislaw.com"},
}
NEWS_SENDER = "news@updates.examplesaas.com"  # newsletter; not a tracked person (noise)


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

def attachment(fn: str, mt: str, size: int) -> dict:
    return {"sha256": sha(fn), "filename": fn, "mimetype": mt, "size_bytes": size}


# Each entry = one message. Wire fields mirror what L0 receives; "gold" carries the labels.
# true_thread groups messages into their correct conversation; project = gold cluster label(s).
ATT_CUTOVER = attachment("cutover_plan.xlsx", "application/vnd.ms-excel", 24000)  # shared by T1 & T3

SCENARIO = [
    # ── T1  Atlas cutover (project: atlas) ───────────────────────────────────
    {"mid": "<atlas-1@acme.com>", "ptid": "pt_atlas1", "from": "jenna@acme.com",
     "to": ["alex@acme.com", "raj@acme.com"], "date": "2026-04-01T09:00:00Z",
     "subject": "Atlas Migration: cutover plan",
     "body": "Hi team, attaching the cutover plan for Atlas. Targeting the 5th.\n\nJenna Park\nStaff Engineer, Acme",
     "atts": [ATT_CUTOVER],
     "gold": {"true_thread": "T1", "project": ["atlas"], "sender": "p_jenna",
              "sensitivity": ["none"], "noise": False,
              "event": {"actor": "p_jenna", "type": "did", "summary": "Sent the Atlas cutover plan"}}},
    {"mid": "<atlas-2@acme.com>", "ptid": "pt_atlas1", "from": "raj@acme.com",
     "to": ["jenna@acme.com", "alex@acme.com"], "date": "2026-04-01T11:30:00Z",
     "subject": "Re: Atlas Migration: cutover plan", "irt": "<atlas-1@acme.com>", "refs": ["<atlas-1@acme.com>"],
     "body": ("I'll re-shard the index before cutover.\n\n"
              "On Wed, Apr 1, Jenna Park <jenna@acme.com> wrote:\n"
              "> Hi team, attaching the cutover plan for Atlas. Targeting the 5th.\n\n"
              "--\nRaj Patel | Acme Engineering | raj@acme.com"),
     "atts": [],
     "gold": {"true_thread": "T1", "project": ["atlas"], "sender": "p_raj",
              "sensitivity": ["none"], "noise": False,
              "event": {"actor": "p_raj", "type": "proposed", "summary": "Will re-shard the index before cutover"},
              "clean_startswith": "I'll re-shard the index before cutover.",
              "clean_must_not_contain": "Hi team, attaching"}},  # quote + signature must be stripped
    {"mid": "<atlas-3@acme.com>", "ptid": "pt_atlas1", "from": "alex@acme.com",
     "to": ["jenna@acme.com", "raj@acme.com"], "date": "2026-04-02T08:15:00Z",
     "subject": "Re: Atlas Migration: cutover plan", "irt": "<atlas-2@acme.com>",
     "refs": ["<atlas-1@acme.com>", "<atlas-2@acme.com>"],
     "body": "Approved. Let's proceed with the 5th.", "atts": [],
     "gold": {"true_thread": "T1", "project": ["atlas"], "sender": "p_alex",
              "sensitivity": ["none"], "noise": False}},

    # ── T2  Atlas vendor SOW (project: atlas; subject drift, no overlap w/ T1 subject) ──
    {"mid": "<atlas-sow-1@datapipe.com>", "ptid": "pt_atlas2", "from": "ben@datapipe.com",
     "to": ["alex@acme.com", "jenna@acme.com"], "date": "2026-04-03T14:00:00Z",
     "subject": "DataPipe migration SOW — redlines",
     "body": "Attaching the revised SOW for the Atlas data migration. See section 3 for the cutover window.",
     "atts": [attachment("sow_datapipe.pdf", "application/pdf", 88000)],
     "gold": {"true_thread": "T2", "project": ["atlas"], "sender": "p_ben",
              "sensitivity": ["none"], "noise": False}},
    {"mid": "<atlas-sow-2@acme.com>", "ptid": "pt_atlas2", "from": "jenna@acme.com",
     "to": ["ben@datapipe.com", "alex@acme.com"], "date": "2026-04-03T16:20:00Z",
     "subject": "Re: DataPipe migration SOW — redlines", "irt": "<atlas-sow-1@datapipe.com>",
     "refs": ["<atlas-sow-1@datapipe.com>"],
     "body": "Section 3 looks fine. One nit on the rollback clause.", "atts": [],
     "gold": {"true_thread": "T2", "project": ["atlas"], "sender": "p_jenna",
              "sensitivity": ["none"], "noise": False}},

    # ── T3  Atlas sign-off (project: atlas; linked to T1 via shared attachment) ──
    {"mid": "<atlas-signoff-1@acme.com>", "ptid": "pt_atlas3", "from": "grace@acme.com",
     "to": ["alex@acme.com"], "date": "2026-04-06T10:00:00Z",
     "subject": "FWD: final cutover sign-off",
     "body": "Staging cutover completed and verified over the weekend. Signing off.",
     "atts": [ATT_CUTOVER],  # same sha as T1 — clustering shared-artifact signal
     "gold": {"true_thread": "T3", "project": ["atlas"], "sender": "p_grace",
              "sensitivity": ["none"], "noise": False,
              "event": {"actor": "p_grace", "type": "outcome", "summary": "Staging cutover completed and verified"}}},

    # ── T4  Borealis kickoff (project: borealis) ─────────────────────────────
    {"mid": "<bor-1@acme.com>", "ptid": "pt_bor1", "from": "aiko@acme.com",
     "to": ["alex@acme.com", "marcus@cloudpeak.io"], "date": "2026-03-10T09:00:00Z",
     "subject": "Borealis Launch kickoff",
     "body": "Kicking off Borealis. Marcus, excited to have Cloudpeak as a launch partner.", "atts": [],
     "gold": {"true_thread": "T4", "project": ["borealis"], "sender": "p_aiko",
              "sensitivity": ["none"], "noise": False}},
    {"mid": "<bor-2@cloudpeak.io>", "ptid": "pt_bor1", "from": "marcus@cloudpeak.io",
     "to": ["aiko@acme.com", "alex@acme.com"], "date": "2026-03-10T15:00:00Z",
     "subject": "Re: Borealis Launch kickoff", "irt": "<bor-1@acme.com>", "refs": ["<bor-1@acme.com>"],
     "body": "Thrilled to partner. Sending over our requirements this week.", "atts": [],
     "gold": {"true_thread": "T4", "project": ["borealis"], "sender": "p_marcus",
              "sensitivity": ["none"], "noise": False}},

    # ── T5  Borealis copy review (project: borealis; MISSING Message-ID → synth) ──
    {"mid": None, "ptid": "pt_bor2", "from": "aiko@acme.com",
     "to": ["alex@acme.com"], "date": "2026-03-18T12:00:00Z",
     "subject": "Borealis launch copy review",
     "body": "Draft launch copy attached for review.",
     "atts": [attachment("borealis_copy_v2.docx", "application/msword", 15000)],
     "gold": {"true_thread": "T5", "project": ["borealis"], "sender": "p_aiko",
              "sensitivity": ["none"], "noise": False, "synthetic_message_id": True}},

    # ── T6  Q3 Renewals — Northwind (project: renewals) ──────────────────────
    {"mid": "<ren-1@northwind.com>", "ptid": "pt_ren1", "from": "dana@northwind.com",
     "to": ["alex@acme.com"], "date": "2026-05-02T13:00:00Z",
     "subject": "Q3 Renewals — Northwind",
     "body": "Renewal paperwork for Q3 is ready. Can you confirm seat count?", "atts": [],
     "gold": {"true_thread": "T6", "project": ["renewals"], "sender": "p_dana",
              "sensitivity": ["none"], "noise": False}},
    {"mid": "<ren-2@acme.com>", "ptid": "pt_ren1", "from": "alex@acme.com",
     "to": ["dana@northwind.com"], "date": "2026-05-02T17:30:00Z",
     "subject": "Re: Q3 Renewals — Northwind", "irt": "<ren-1@northwind.com>", "refs": ["<ren-1@northwind.com>"],
     "body": "Confirmed: 240 seats.", "atts": [],
     "gold": {"true_thread": "T6", "project": ["renewals"], "sender": "p_alex",
              "sensitivity": ["none"], "noise": False}},

    # ── T7  weekly sync (MULTI-PROJECT: atlas + borealis; uses j.park alias → identity merge) ──
    {"mid": "<sync-1@acme.com>", "ptid": "pt_sync1", "from": "j.park@acme.com",
     "to": ["alex@acme.com", "aiko@acme.com", "grace@acme.com"], "date": "2026-04-04T16:00:00Z",
     "subject": "Weekly sync notes",
     "body": "Notes: Atlas cutover on track for the 5th; Borealis copy review pending Aiko.", "atts": [],
     "gold": {"true_thread": "T7", "project": ["atlas", "borealis"], "sender": "p_jenna",
              "sensitivity": ["none"], "noise": False}},

    # ── T8 & T9  subject-reuse: SAME subject + SAME provider_thread_id, UNRELATED lineage ──
    #            reconstruction must keep them SEPARATE and set lineage_conflict.
    {"mid": "<qq-dana@northwind.com>", "ptid": "pt_dup", "from": "dana@northwind.com",
     "to": ["alex@acme.com"], "date": "2026-05-05T09:00:00Z",
     "subject": "Re: quick question",
     "body": "Quick one on the renewal discount tier — can we do 12%?", "atts": [],
     "gold": {"true_thread": "T8", "project": ["renewals"], "sender": "p_dana",
              "sensitivity": ["none"], "noise": False, "lineage_conflict": True}},
    {"mid": "<qq-marcus@cloudpeak.io>", "ptid": "pt_dup", "from": "marcus@cloudpeak.io",
     "to": ["alex@acme.com"], "date": "2026-05-06T09:00:00Z",
     "subject": "Re: quick question",
     "body": "Quick one on Borealis — when does the partner portal go live?", "atts": [],
     "gold": {"true_thread": "T9", "project": ["borealis"], "sender": "p_marcus",
              "sensitivity": ["none"], "noise": False, "lineage_conflict": True}},

    # ── T10  newsletter (NOISE) ──────────────────────────────────────────────
    {"mid": "<news-1@updates.examplesaas.com>", "ptid": "pt_news1", "from": NEWS_SENDER,
     "to": ["alex@acme.com"], "date": "2026-04-15T06:00:00Z",
     "subject": "ExampleSaaS Monthly: 7 tips for power users",
     "body": "Read our latest tips. Click here to learn more.", "atts": [],
     "headers": {"List-Unsubscribe": "<mailto:unsub@updates.examplesaas.com>", "Precedence": "bulk"},
     "gold": {"true_thread": "T10", "project": [], "sender": None,
              "sensitivity": ["none"], "noise": True}},

    # ── T11  HR (SENSITIVITY: hr) ────────────────────────────────────────────
    {"mid": "<hr-1@acme.com>", "ptid": "pt_hr1", "from": "hr@acme.com",
     "to": ["alex@acme.com"], "date": "2026-04-20T10:00:00Z",
     "subject": "Your 2026 compensation review",
     "body": "Your updated salary and benefits enrollment details for the 2026 performance review cycle.",
     "atts": [],
     "gold": {"true_thread": "T11", "project": [], "sender": "p_hr",
              "sensitivity": ["hr"], "noise": False}},

    # ── T12  Legal (SENSITIVITY: privileged + legal) ─────────────────────────
    {"mid": "<legal-1@morrislaw.com>", "ptid": "pt_legal1", "from": "counsel@morrislaw.com",
     "to": ["alex@acme.com", "grace@acme.com"], "date": "2026-04-22T11:00:00Z",
     "subject": "Privileged and confidential: DataPipe contract review",
     "body": "Attorney-client privileged. Our review of the DataPipe agreement is attached.",
     "atts": [attachment("contract_review.pdf", "application/pdf", 120000)],
     "gold": {"true_thread": "T12", "project": [], "sender": "p_legal",
              "sensitivity": ["privileged", "legal"], "noise": False}},

    # ── T13  prospect inbound from Jenna Brooks (role: lead) ─────────────────
    #         NAME COLLISION: same local-part "jenna" + same first name as Jenna Park,
    #         different domain/surname → identity resolution must NOT merge them.
    {"mid": "<eval-1@vertexlabs.com>", "ptid": "pt_eval1", "from": "jenna@vertexlabs.com",
     "to": ["alex@acme.com"], "date": "2026-03-12T10:00:00Z",
     "subject": "Interested in Borealis — eval access?",
     "body": "Hi Alex, Jenna Brooks here from Vertex Labs. We'd love early eval access to Borealis.\n\nJenna Brooks\nVertex Labs",
     "atts": [],
     "gold": {"true_thread": "T13", "project": ["borealis"], "sender": "p_jbrooks",
              "sensitivity": ["none"], "noise": False}},
]


def display_name(email: str) -> str:
    for p in PEOPLE.values():
        if email in p["addresses"]:
            return p["name"]
    return email.split("@")[0]


def build_mailbox() -> dict:
    messages = []
    for i, m in enumerate(SCENARIO):
        headers = dict(m.get("headers", {}))
        if m.get("mid"):
            headers["Message-ID"] = m["mid"]
        headers["From"] = f"{display_name(m['from'])} <{m['from']}>"
        headers["To"] = ", ".join(f"{display_name(a)} <{a}>" for a in m.get("to", []))
        if m.get("cc"):
            headers["Cc"] = ", ".join(f"{display_name(a)} <{a}>" for a in m["cc"])
        headers["Date"] = m["date"]
        headers["Subject"] = m["subject"]
        if m.get("irt"):
            headers["In-Reply-To"] = m["irt"]
        if m.get("refs"):
            headers["References"] = " ".join(m["refs"])
        messages.append({
            "provider_id": f"pmsg_{i:03d}",
            "provider_thread_id": m["ptid"],
            "headers": headers,
            "body_text": m["body"],
            "attachments": m.get("atts", []),
        })
    return {"owner_email": OWNER_EMAIL, "internal_domains": INTERNAL_DOMAINS, "messages": messages}


def build_gold() -> dict:
    pid_of = {i: f"pmsg_{i:03d}" for i in range(len(SCENARIO))}
    identities, roles = {}, {}
    for pid, p in PEOPLE.items():
        roles[pid] = p["role"]
        for a in p["addresses"]:
            identities[a] = pid
    threads, projects, sensitivity, noise = {}, {}, {}, {}
    lineage_conflict, synthetic_ids, events, clean_checks = [], [], [], {}
    for i, m in enumerate(SCENARIO):
        g, pmsg = m["gold"], pid_of[i]
        threads.setdefault(g["true_thread"], []).append(pmsg)
        projects[g["true_thread"]] = g["project"]
        sensitivity[pmsg] = g["sensitivity"]
        noise[pmsg] = g["noise"]
        if g.get("lineage_conflict"):
            lineage_conflict.append(m["ptid"])
        if g.get("synthetic_message_id"):
            synthetic_ids.append(pmsg)
        if g.get("event"):
            events.append({**g["event"], "source": pmsg})
        if "clean_startswith" in g:
            clean_checks[pmsg] = {"startswith": g["clean_startswith"],
                                  "must_not_contain": g["clean_must_not_contain"]}
    return {
        "identities": {
            "address_to_person": identities,
            "must_merge": [["jenna@acme.com", "j.park@acme.com"]],
            "must_not_merge": [["jenna@acme.com", "jenna@vertexlabs.com"]],  # same first name, different people
        },
        "roles": roles,
        "threads": {
            "members_by_provider_id": threads,
            "shared_provider_thread_id_but_separate": sorted(set(lineage_conflict)),  # expect lineage_conflict=True
            "synthetic_message_id": synthetic_ids,
        },
        "projects": projects,        # true_thread -> [project labels]; spec 03 clustering gold
        "sensitivity": sensitivity,  # provider_id -> [tags]
        "noise": noise,              # provider_id -> bool
        "events": events,
        "clean_text_checks": clean_checks,  # quote/signature stripping assertions (spec 00 §19)
    }


def main():
    (OUT / "gold").mkdir(exist_ok=True)
    (OUT / "mailbox.json").write_text(json.dumps(build_mailbox(), indent=2))
    gold = build_gold()
    for name, payload in gold.items():
        (OUT / "gold" / f"{name}.json").write_text(json.dumps(payload, indent=2))
    print(f"wrote mailbox.json ({len(SCENARIO)} messages) and gold/{{{','.join(gold)}}}.json")


if __name__ == "__main__":
    main()
