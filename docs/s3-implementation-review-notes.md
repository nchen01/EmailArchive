# S3 Implementation Review Notes

This repo is ready to continue implementation. The setup and schema-package cleanup are solid enough to move forward without carrying a broken install path.

## Recommended Build Order

Start with **spec 03 project clustering**, not project view.

Project view depends on real `Project` and `ThreadProjectAssignment` output. Building the UI first risks hard-coded shapes or fake project data, which this repo explicitly avoids.

Implement spec 03 tickets in order:

1. `features.py`
2. `blocking.py`
3. `similarity.py`
4. similarity graph
5. Leiden community detection
6. soft membership
7. materialization and confidence
8. labeling
9. incremental ID carry-over
10. eval
11. pipeline and CLI

Do not skip straight to `pipeline.py`; the acceptance gates depend on each piece being testable independently.

## Things To Beware Of

### Dependencies

Spec 03 likely needs dependencies not currently in the root install, such as `numpy`, `scipy`, `scikit-learn`, `spacy`, `sentence-transformers`, `hnswlib`, `python-igraph`, and `leidenalg`.

Add them deliberately, probably as a `clustering` extra in `pyproject.toml`. If local S3 work needs them, make `dev` include them explicitly. Do not rely on a global Python environment.

### Embeddings

Spec 03 says the embedding model and dimension must match L2. L2 is not built and the embedding model is deferred.

For S3, use an injectable `embed_fn` with deterministic fixture/test embeddings, plus clearly named placeholder config if needed. Do not download or call a live model inside tests.

### Determinism

Determinism is non-negotiable:

- fixed `seed`
- stable sorting everywhere
- no wall-clock time in logic paths
- no unordered `set`/`dict` iteration leaking into IDs or serialized output

Add a test that runs clustering twice and compares byte-identical serialized output.

### Owner Handling

`Thread.participants` includes the owner. Clustering features must exclude the owner.

Strip the owner exactly once in L1 feature construction. Do not strip the owner in L0, the API, or multiple clustering stages.

### Schema Boundaries

Import persisted and cross-service shapes from `ekc_schemas`.

`ThreadFeatures` is compute-only and should live under `services/enrich/clustering/features.py`, not in schemas. Output must use `ClusteringResult`, `Project`, and `ThreadProjectAssignment` from `ekc_schemas`.

### Precision Over Recall

The spec prefers under-merging over merging unrelated work. Watch especially for:

- shared vendors across unrelated projects
- recurring sync threads
- internal-heavy threads
- email volume being mistaken for project importance

Wrong merges corrupt project view and "who to ask" more severely than conservative splits.

### Eval Before Victory

Build the eval before declaring the clustering work done.

Spec 03 requires:

- extended-BCubed F1 >= 0.75
- extended-BCubed precision >= 0.80
- >=80% of multi-gold threads receive >=2 predicted projects
- label quality >= 80% on the seed fixture
- deterministic byte-identical output across repeated runs

Track and report Omega as a secondary metric, but do not treat Omega >= 0.70 as a hard DoD gate unless the spec is amended to say so.

If the eval does not exist, "looks good on the fixture" is not done.

Also include the explicit spec 03 DoD items:

- sparse-mailbox fallback path covered by a test for fewer than 30 threads
- `--debug` emits `cluster_cards.json` and `similarity_graph.graphml`

### Project View Boundary

Spec 02 includes activity/events, but "What's been done" is S4 because event extraction is not implemented.

For S3 project view, render:

- project metadata
- members
- who to ask
- recent threads

Do not fake `Event`s or L3 summaries.

## Review Preference

Commit after each spec ticket, or after a small coherent group of tickets.

Now that the repo is Git-backed, small commits make reviews sharper and catch spec drift early.
