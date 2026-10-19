"""The DAG layer contract, checked against a parsed dbt manifest (MLB-158 Phase C).

The contract (docs/dag-boundaries.md section 1): every model carries a layer in
config.meta.dag_layer, and a model reads only its own layer or an earlier one,
in the order staging -> identity -> reconstruction -> convergence -> core ->
reporting. Edges are the model-to-model entries of depends_on.nodes; sources
and seeds are upstream inputs and never count. The one forward skip that needs
permission is reporting reading staging directly: each such edge must be a row
in dbt_league/dag_exceptions.yml, and each row there must still be an edge.

The folder tree and the name prefixes are deliberately NOT inputs. The
manifest is what dbt, the lineage view and a reviewer all read; four model
names contradict their layer on purpose (names are interfaces).

usage:
    python tools/dag_boundaries.py --manifest dbt_league/target/manifest.json
        [--register dbt_league/dag_exceptions.yml] [--readme README.md]
        [--doc docs/dag-boundaries.md]

Prints a short summary, one line per active gap, one line per failure, and
exits 1 on any failure. tests/test_dag_boundaries.py calls the same functions.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ORDER = ['staging', 'identity', 'reconstruction', 'convergence', 'core', 'reporting']
KINDS = ('audit', 'gap')


# ------------------------------------------------------------------ manifest

def load_manifest(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def models(manifest):
    """unique_id -> model node, for this project's models only."""
    return {uid: n for uid, n in manifest['nodes'].items() if n['resource_type'] == 'model'}


def layers(manifest):
    """model name -> its meta.dag_layer (None where the model has none)."""
    return {n['name']: (n['config'].get('meta') or {}).get('dag_layer') for n in models(manifest).values()}


def missing_layers(manifest):
    """Models with no layer, or a value outside the six: each one a failure."""
    return sorted(name for name, layer in layers(manifest).items() if layer not in ORDER)


def model_edges(manifest):
    """Sorted, de-duplicated (reader, input) pairs between models."""
    ms = models(manifest)
    edges = set()
    for n in ms.values():
        for dep in n['depends_on']['nodes']:
            if dep in ms:
                edges.add((n['name'], ms[dep]['name']))
    return sorted(edges)


def classify(manifest):
    """Every model edge with its kind, the Phase A / B1 recount method:
    backward (input in a later layer), kind_d (reporting reads staging),
    same_layer, forward. Edges touching a model without a valid layer are
    'unclassified'."""
    layer = layers(manifest)
    out = []
    for reader, dep in model_edges(manifest):
        lr, ld = layer.get(reader), layer.get(dep)
        if lr not in ORDER or ld not in ORDER:
            kind = 'unclassified'
        elif ORDER.index(ld) > ORDER.index(lr):
            kind = 'backward'
        elif lr == 'reporting' and ld == 'staging':
            kind = 'kind_d'
        elif lr == ld:
            kind = 'same_layer'
        else:
            kind = 'forward'
        out.append(dict(reader=reader, reader_layer=lr, input=dep, input_layer=ld, kind=kind))
    return out


def backward_edges(manifest):
    return [(e['reader'], e['input']) for e in classify(manifest) if e['kind'] == 'backward']


def reporting_to_staging_edges(manifest):
    return [(e['reader'], e['input']) for e in classify(manifest) if e['kind'] == 'kind_d']


def layer_counts(manifest):
    layer = layers(manifest)
    return {l: sum(1 for v in layer.values() if v == l) for l in ORDER}


def exposure_names(manifest):
    return sorted(e['name'] for e in manifest.get('exposures', {}).values())


# ------------------------------------------------------------------ register

def load_register(path):
    import yaml   # PyYAML ships with dbt-core, so it is in every environment that can parse
    doc = yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}
    return doc.get('exceptions') or []


def register_problems(entries, manifest):
    """Malformed rows: unknown kind, a gap with no expiry model, an audit with
    one, a duplicated pair, or a row whose reader is not reporting or whose
    input is not staging (only that skip is registrable)."""
    layer = layers(manifest)
    problems, seen = [], set()
    for i, e in enumerate(entries, 1):
        pair = (e.get('reader'), e.get('input'))
        where = f'register row {i} ({pair[0]} -> {pair[1]})'
        if not pair[0] or not pair[1]:
            problems.append(f'{where}: needs both reader and input')
            continue
        if pair in seen:
            problems.append(f'{where}: duplicate row')
        seen.add(pair)
        kind = e.get('kind')
        if kind not in KINDS:
            problems.append(f'{where}: kind must be audit or gap, not {kind!r}')
        if kind == 'gap' and not e.get('expires_when_model_exists'):
            problems.append(f'{where}: a gap row needs expires_when_model_exists')
        if kind == 'audit' and e.get('expires_when_model_exists'):
            problems.append(f'{where}: an audit row is permanent and carries no expiry model')
        if not str(e.get('reason') or '').strip():
            problems.append(f'{where}: needs a reason')
        if pair[0] in layer and layer[pair[0]] != 'reporting':
            problems.append(f'{where}: reader is {layer[pair[0]]}, only reporting -> staging is registrable')
        if pair[1] in layer and layer[pair[1]] != 'staging':
            problems.append(f'{where}: input is {layer[pair[1]]}, only reporting -> staging is registrable')
    return problems


def compare_register(manifest, entries):
    """(unregistered, retired): manifest skips with no row, rows with no skip."""
    have = set(reporting_to_staging_edges(manifest))
    rows = {(e.get('reader'), e.get('input')) for e in entries}
    return sorted(have - rows), sorted(rows - have)


def gaps(manifest, entries):
    """(expired, active): gap rows whose expiry model now exists, and the rest."""
    names = set(layers(manifest))
    expired, active = [], []
    for e in entries:
        if e.get('kind') != 'gap':
            continue
        target = e.get('expires_when_model_exists')
        (expired if target in names else active).append(
            (e.get('reader'), e.get('input'), target))
    return expired, active


# ------------------------------------------------------------- docs and README

def doc_register(path):
    """The (reader, input) -> kind rows of docs/dag-boundaries.md's exception
    register: the table rows between the register heading and the next
    second-level heading, kind taken from the bold group header above them,
    plus the text of each row (for the expiry-name check)."""
    text = Path(path).read_text(encoding='utf-8')
    m = re.search(r'^### Exact-edge .*?exception register\s*$(.*?)(?=^## )', text, re.M | re.S)
    if not m:
        raise ValueError(f'{path}: no "### Exact-edge ... exception register" section found')
    rows, kind = {}, None
    for line in m.group(1).splitlines():
        if line.startswith('**Audits'):
            kind = 'audit'
        elif line.startswith('**Modeling gaps'):
            kind = 'gap'
        cells = re.match(r'^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|(.*)\|\s*$', line)
        if cells:
            rows[(cells.group(1), cells.group(2))] = dict(kind=kind, text=cells.group(3))
    return rows


def compare_doc(doc_rows, entries):
    """Differences between the doc's register and the YAML: missing or extra
    pairs, a kind that disagrees, a gap row whose prose does not name its
    expiry model."""
    problems = []
    yrows = {(e.get('reader'), e.get('input')): e for e in entries}
    for pair in sorted(set(yrows) - set(doc_rows)):
        problems.append(f'{pair[0]} -> {pair[1]} is in the register but not in the doc')
    for pair in sorted(set(doc_rows) - set(yrows)):
        problems.append(f'{pair[0]} -> {pair[1]} is in the doc but not in the register')
    for pair in sorted(set(yrows) & set(doc_rows)):
        y, d = yrows[pair], doc_rows[pair]
        if y.get('kind') != d['kind']:
            problems.append(f'{pair[0]} -> {pair[1]}: register says {y.get("kind")}, doc says {d["kind"]}')
        target = y.get('expires_when_model_exists')
        if y.get('kind') == 'gap' and target and f'`{target}`' not in d['text']:
            problems.append(f'{pair[0]} -> {pair[1]}: the doc row does not name its expiry model `{target}`')
    return problems


def readme_numbers(path):
    """Numbers the root README's Architecture section states: '<layer>: <n>
    models' for each layer inside its mermaid block, '<n> dbt exposures' in
    the block, and '<n> dbt models' in the section."""
    text = Path(path).read_text(encoding='utf-8')
    sec = re.search(r'^## Architecture\s*$(.*?)(?=^## |\Z)', text, re.M | re.S)
    if not sec:
        raise ValueError(f'{path}: no "## Architecture" section')
    block = re.search(r'```mermaid\s*\n(.*?)```', sec.group(1), re.S)
    if not block:
        raise ValueError(f'{path}: no mermaid block in the Architecture section')
    found = {}
    for layer in ORDER:
        hits = re.findall(rf'\b{layer}: (\d+) models\b', block.group(1))
        found[layer] = [int(h) for h in hits]
    found['exposures'] = [int(h) for h in re.findall(r'\b(\d+) dbt exposures\b', block.group(1))]
    found['total'] = [int(h) for h in re.findall(r'\b(\d+) dbt models\b', sec.group(1))]
    return found


def compare_readme(manifest, found):
    """One line per README number that disagrees with the manifest, naming the
    label and both numbers; a label that is missing or stated twice also fails."""
    want = dict(layer_counts(manifest))
    want['exposures'] = len(exposure_names(manifest))
    want['total'] = len(layers(manifest))
    label = {'exposures': 'dbt exposures', 'total': 'dbt models (total)'}
    problems = []
    for key, n in want.items():
        got = found.get(key, [])
        name = label.get(key, key)
        if len(got) != 1:
            problems.append(f'README {name}: expected one stated number, found {len(got)} {got}')
        elif got[0] != n:
            problems.append(f'README {name}: states {got[0]}, the manifest has {n}')
    return problems


# --------------------------------------------------------------------- check

def check(manifest, register_path=None, readme_path=None, doc_path=None):
    """Everything the CLI and the test assert, as data."""
    failures, warnings = [], []
    for name in missing_layers(manifest):
        failures.append(f'no valid meta.dag_layer: {name}')
    for r, d in backward_edges(manifest):
        failures.append(f'backward edge: {r} ({layers(manifest)[r]}) reads {d} ({layers(manifest)[d]})')
    kinds = {}
    for e in classify(manifest):
        kinds[e['kind']] = kinds.get(e['kind'], 0) + 1
    summary = dict(models=len(layers(manifest)), layer_counts=layer_counts(manifest),
                   model_edges=len(model_edges(manifest)),
                   edge_kinds={k: kinds.get(k, 0) for k in
                               ('backward', 'kind_d', 'same_layer', 'forward', 'unclassified')},
                   exposures=len(exposure_names(manifest)), exposure_names=exposure_names(manifest))
    if register_path:
        entries = load_register(register_path)
        failures += register_problems(entries, manifest)
        unregistered, retired = compare_register(manifest, entries)
        failures += [f'unregistered reporting-to-staging edge: {r} -> {d}' for r, d in unregistered]
        failures += [f'registered pair no longer in the manifest, retire the row: {r} -> {d}' for r, d in retired]
        expired, active = gaps(manifest, entries)
        failures += [f'the exception has expired: {t} exists, so {r} -> {d} must move onto it and the row '
                     f'must be retired' for r, d, t in expired]
        warnings += [f'exception until {t}: {r} -> {d}' for r, d, t in active]
        summary.update(registered=len(entries),
                       registered_by_kind={k: sum(1 for e in entries if e.get('kind') == k) for k in KINDS},
                       unregistered=len(unregistered), retired=len(retired),
                       expired=len(expired), active_gaps=len(active))
    if doc_path and register_path:
        doc_problems = compare_doc(doc_register(doc_path), load_register(register_path))
        failures += [f'doc register: {p}' for p in doc_problems]
        summary['doc_register_matches'] = not doc_problems
    if readme_path:
        readme_problems = compare_readme(manifest, readme_numbers(readme_path))
        failures += readme_problems
        summary['readme_numbers_match'] = not readme_problems
    summary['unapproved'] = summary['edge_kinds']['backward'] + summary['edge_kinds']['unclassified'] + \
        summary.get('unregistered', 0)
    return dict(summary=summary, failures=failures, warnings=warnings)


def render(result):
    s = result['summary']
    lc = ', '.join(f'{l} {n}' for l, n in s['layer_counts'].items())
    lines = [f"{s['models']} models: {lc}",
             f"{s['model_edges']} model edges: {s['edge_kinds']['backward']} backward, "
             f"{s['edge_kinds']['kind_d']} reporting-to-staging, {s['edge_kinds']['same_layer']} same-layer, "
             f"{s['edge_kinds']['forward']} forward"]
    if 'registered' in s:
        k = s['registered_by_kind']
        lines.append(f"{s['registered']} registered ({k['audit']} audit, {k['gap']} gap); "
                     f"{s['unapproved']} unapproved; {s['retired']} to retire; {s['expired']} expired; "
                     f"{s['active_gaps']} active gaps")
    else:
        lines.append(f"{s['unapproved']} unapproved (no register given: reporting-to-staging edges not judged)")
    if 'doc_register_matches' in s:
        lines.append('doc register matches' if s['doc_register_matches'] else 'doc register DIFFERS')
    if 'readme_numbers_match' in s:
        lines.append('README numbers match' if s['readme_numbers_match'] else 'README numbers DIFFER')
    lines.append(f"{s['exposures']} exposures")
    lines += [f'WARN  {w}' for w in result['warnings']]
    lines += [f'FAIL  {f}' for f in result['failures']]
    lines.append('OK' if not result['failures'] else f"FAILED: {len(result['failures'])} problem(s)")
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--manifest', required=True, help='a manifest.json written by dbt parse')
    ap.add_argument('--register', help='dbt_league/dag_exceptions.yml')
    ap.add_argument('--readme', help='the root README.md, whose Architecture map is checked')
    ap.add_argument('--doc', help='docs/dag-boundaries.md, whose register must equal the YAML')
    args = ap.parse_args(argv)
    result = check(load_manifest(args.manifest), args.register, args.readme, args.doc)
    print(render(result))
    return 1 if result['failures'] else 0


if __name__ == '__main__':
    sys.exit(main())
