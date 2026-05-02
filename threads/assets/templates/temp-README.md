# temp/ — regeneratable outputs

Everything in this directory is gitignored. Use the commands below
to regenerate.

## Gate-dependent fixtures

Outputs stay disposable only while no committed test or CI gate
loads their exact bytes. If a gate depends on a specific snapshot,
promote that file to `../data/` and document the source command and
consumer in `../data/README.md`, or make the gate regenerate
byte-identical output as part of its setup.

| Output | Command |
|---|---|
| *(none yet — commands get added as diagnostics produce outputs)* | |
