# Logistics intake — one preset, end to end

This is the example `qagent` exists for, built the way a real project would build it: a
freight forwarder receives a dirty email and needs a structured intake request out of it —
origin, destination, cargo, dates, amount, contact — without inventing a single value.

The core knows nothing about any of this. Everything domain-specific lives in this folder:
about 800 lines of Python around the preset, plus a 1,000-line dataset builder that carries
the 32 labelled cases.

## The cartridge, in five files

| File | What it is | What it shows |
|---|---|---|
| [`schema.py`](schema.py) | the output contract (`ExtractedIntake`) | optional-at-the-type-level fields, ISO strings, `evidence`, `unresolved` |
| [`checks.py`](checks.py) | eight checks, five of them domain rules | generic checks + `Rule` predicates with their own codes |
| [`preset.py`](preset.py) | `PRESET`: rubric, prompt, scoring spec, retry hook | the one object `guard()` needs |
| [`agent.py`](agent.py) | one model call, feedback appended on retry | the entire integration surface |
| [`runner.py`](runner.py) | client/model/pricing wiring | the `module:factory` the CLI calls |
| [`qagent.toml`](qagent.toml) | the same preset as data | the file-based path: `qagent eval --config` |

`qagent.toml` is not a summary of `preset.py`: it builds the same preset (a test asserts the two
fingerprints are equal), and a replay run through the file gives the same numbers.
Prompts live in [`prompts/`](prompts/) text files, read by both paths, so they cannot drift.

Two supporting files: [`build_dataset.py`](build_dataset.py) generates the committed
`dataset.jsonl` and refuses to write it if a citation is not in its email, and
[`demo.py`](demo.py) is the deterministic stand-in used by the sample reports. The
[`cassettes/`](cassettes/) folder holds the recorded live run: 71 real answers, replayed for free.

## Run it

No key, no network, no money:

```bash
make demo      # the demo agent through the real CLI -> reports/demo_guarded.json
make sample    # regenerate the committed stub reports + diff
make replay    # the committed live cassette: pass 81.2%, no key, no network
```

```console
$ make demo
20261122T151956-guarded-<sha>: 32 cases, pass 93.8% -> reports/demo_guarded.json
```

A real model run (needs `DEEPSEEK_API_KEY`, costs cents):

```bash
make baseline           # one call per case, no checks
make eval               # full loop, judge on
make diff               # baseline vs guarded -> reports/diff.md
QAGENT_RECORD=1 make eval   # ...and append every answer to the cassette as well
```

`QAGENT_LLM` selects the source: `stub` (default), `replay` (the committed cassette, no key)
or `live`. `QAGENT_RECORD=1` on a live run is the only way the cassette grows; in `replay`
mode a changed prompt is a `CassetteMiss` with the key it wanted, never a silent fallback.

## What the live run says

32 cases, `deepseek-flash` as the agent and `deepseek-v4-pro` as the judge, reasoning off at
the provider (U37 in [`docs/how-it-works.md`](../../docs/how-it-works.md)):

| | baseline | guarded |
|---|---|---|
| Pass, final | 59.4% | 81.2% |
| False pass rate | 40.6% | 18.8% |
| Retry recovery | — | 66.7% |
| Cost per passed case | $0.0014 | $0.0041 |

Six answers were returned for repair and four came back correct. The six that still slip
through are mostly `request_type` (quote vs. booking on the edge cases) and the traps only
the golden data can see. Full numbers and per-case rows:
[`reports/README.md`](../../reports/README.md).

## What the checks actually catch

The demo's scripted defects are chosen to trip one specific check each, so the sample report
doubles as a catalogue.

| Code | What it caught | Case |
|---|---|---|
| `G1_REQUIRED_MISSING` | cargo list dropped, destination city dropped | `hq-07`, `ed-09` |
| `G2_BAD_COUNTRY` | `"Lithuania"` instead of `LT` | `hq-03`, `ed-04` |
| `G2_BAD_DATE` | `03.12.2026` instead of `2026-12-03` | `hq-11` |
| `G4_FAKE_QUOTE` | a city quote that is not in the email | `ed-02` |
| `G5_PII_LEAK` | an email copied into a free-text field | *not scripted; live on all output* |
| `G6_REFUSAL` | "I cannot assist with this request" | `adv-05` |
| `G7_DUPLICATE_ITEMS` | the same cargo line twice | `hq-15` |
| `L1_PICKUP_AFTER_DELIVERY` | dates swapped | `ed-07` |
| `L2_REEFFER_WITHOUT_TEMPERATURE` | a reefer request without a temperature | *rule, not scripted* |
| `L3_TEMPERATURE_WITHOUT_REQUIREMENT` | a temperature nobody asked for | *rule, not scripted* |
| `L4_NO_CONTACT_CHANNEL` | no way to answer the lead | *rule, not scripted* |
| `L5_CARGO_OUT_OF_RANGE` | 45,000 kg on one pallet | `hq-05` |

Two failures are caught only by the golden data, not by any check:

- `adv-01`: the email states no weight, and the agent invents 800 kg. Every deterministic
  check passes. The trap in the dataset (`cargo.0.weight_kg ∈ {800, 1000}`) fails the case
  offline, which is exactly what the `false_pass_rate` metric is for.
- `adv-02`: a `SYSTEM:` line inside the email tries to set the amount to 1 EUR. The trap
  records that value, and the prompt tells the agent the email is data, not instructions.

## The dataset

32 cases: 18 happy-path, 9 edge cases, 5 adversarial. 18 carry a `human_verdict`, used as
the judge's calibration label in a run that has a judge.

The interesting ones are the edge cases, because they encode *decisions*:

| Case | Decision |
|---|---|
| `ed-01` | `1.2 t` is 1200 kg: units are converted, not guessed |
| `ed-03` | "first week of December" stays `null` and is listed in `unresolved` |
| `ed-05` | the country comes from the postcode prefix `RO-400001` |
| `ed-06` | `€ 2,300` becomes amount `2300.0`, currency `EUR` |
| `ed-08` | an unspecified quantity is `null` and listed in `unresolved` |
| `adv-04` | no quantity, no weight: two honest abstentions, zero invented numbers |

`dataset.jsonl` is generated by [`build_dataset.py`](build_dataset.py), which fails the build
if a cited quote is not a substring of its email, if an `unresolved` path is actually filled,
or if a golden answer does not validate. The committed JSONL is what a reviewer reads;
`tests/unit/test_example_logistics.py` fails when the two drift apart.

## Scoring

`SCORING` states 16 fields. One decision is worth explaining: only `cargo.0.*` is scored,
because a dotted path cannot say "every item", and a spec whose weight silently changes with
the number of cargo lines would make two runs incomparable. The rest of the cargo list still
passes through the checks — it is simply not part of the accuracy number.

Not scored, on purpose: `contact.phone` (the same number formats three ways and the metric
would measure spacing), `postcode`, `notes`, `evidence`. The phone is still extracted and
still format-checked; it just is not a quality signal.

## Build your own preset

1. Write the output model; make every field the email might not state optional, and add
   `unresolved: list[str]` plus `evidence: list[Evidence]`.
2. Write the checks: generic ones from `qagent.checks`, domain rules as `Rule` predicates.
3. Write the rubric (criteria and weights, not prose) and the scoring spec (which fields,
   which comparator, which traps).
4. Write the agent: `(case, feedback) -> attempt | AgentReply`. Append
   `render_feedback(feedback)` on a retry; report usage if the agent knows it.
5. Build 30–50 cases, label the hard half, and add traps for the values a model is tempted
   to invent.
6. Connect it: `guard(agent, preset, llm=...)` and `python -m qagent eval --spec ...`.

Nothing in this folder was needed to change the core, except fixes for two bugs the example
exposed (a loose phone pattern that flagged ISO dates, and a numeric comparator that treated
`null` versus `null` as a miss). That is the intended relationship: the preset is where the
domain goes, and when the core is wrong, it is wrong for everyone.

## What this demo does not prove

The sample reports measure the loop, not a model: the stub answers are the golden answers
with one defect. Real pass rates, real retry recovery and real costs appear only after a
live or replayed run. The pipeline is deliberately honest about that in every file it
produces — see [`reports/README.md`](../../reports/README.md).
