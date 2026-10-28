# Guideline Chunker upstream snapshot

Source: `F:\BrainBloodAgent\01_Code_Archive\Question_Tooling\knowledge_question\guideline_chunker`

Git revision: `13940c9709dac157f846eb072574395084eabb48`.

`guideline_chunker/` contains all 24 upstream package files, copied without code changes.
`guideline_chunker_data/` contains both original YAML configuration files, including the
14-category / 102-tag HICH taxonomy. Local caches, API data, outputs, secrets and git
history are not imported. The source repository is unchanged.

The Medical Knowledge adapter lives in `app/services/guideline_chunking.py`. It uses
the upstream `run_document` pipeline, validators, cache, source-span assembly and
four-file publication contract directly. It maps resulting chunk types, identifiers,
summaries, topic tags and recommendation levels into the native chunk metadata.
Server paths and API credentials are supplied by the host, not user method JSON.

To update: copy a reviewed upstream snapshot, record its revision here and in the
adapter, run upstream offline regression tests against the copied package, then run
the host integration tests. Do not modify the upstream files to implement host APIs.
