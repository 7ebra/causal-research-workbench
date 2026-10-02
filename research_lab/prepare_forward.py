"""Recreate candidate models from the original training partition only."""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
import screener as s


def prepare(results, target):
    if target.exists():
        raise ValueError('Candidate directory exists; frozen models will not be overwritten')
    report = json.loads((results/'report.json').read_text())
    selected = json.loads((results/'model.json').read_text())
    start = datetime.fromisoformat(report['split']['start']).timestamp()
    end = datetime.fromisoformat(report['split']['train_end']).timestamp()
    datasets = {}
    for asset, digest in report['data_hashes'].items():
        file = results/'data_snapshot'/f'{asset}.csv'
        if hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            raise ValueError(f'{asset}: historical input hash changed')
        rows = s.load_candles(file)
        datasets[asset] = (rows, s.features(rows))
    models = {}
    for candidate in report['candidates']:
        asset, minutes = candidate['asset'], candidate['expiry_minutes']
        rows, states = datasets[asset]
        ensemble = s.combine([s.train(rows, states, start, end, minutes//5,
                                     selected['payout_assumed'], seed, selected['epochs'])
                              for seed in selected['seeds']])
        if candidate['key'] == report['selected']['key']:
            if ensemble['q'] != selected['q'] or ensemble['support'] != selected['support']:
                raise ValueError('Recreated selected model differs from the frozen original')
        models[candidate['key']] = dict(ensemble, asset=asset, expiry_minutes=minutes,
            payout_assumed=selected['payout_assumed'], training_end_exclusive=report['split']['train_end'],
            research_only=True, historical_validation_eligible=candidate['validation_eligible'])
        print(f"Frozen {candidate['key']}", flush=True)
    target.mkdir(parents=True)
    hashes = {}
    for key, model in models.items():
        contents = json.dumps(model, sort_keys=True, indent=2).encode()
        (target/f'{key}.json').write_bytes(contents)
        hashes[key] = hashlib.sha256(contents).hexdigest()
    manifest = {'training_end_exclusive':report['split']['train_end'],
                'original_selected_model_sha256':report['model_sha256'], 'hashes':hashes,
                'purpose':'Frozen research candidates; no holdout reevaluation or retuning'}
    (target/'manifest.json').write_text(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--results',type=Path,default=s.ROOT/'results')
    p.add_argument('--out',type=Path,default=s.ROOT/'frozen_candidates')
    args=p.parse_args()
    prepare(args.results,args.out)
