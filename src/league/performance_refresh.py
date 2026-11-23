"""Stage public NBA downloads and a complete forecast before publishing locally."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
import uuid

import numpy as np
import pandas as pd

from src.ingestion.base import validate_game_logs, write_raw
from src.ingestion.csv_source import CSVSource, _is_synthetic, game_log_files
from src.ingestion.nba_api_source import NBAApiSource
from src.league.storage import league_dir
from src.league.service import build_forecast, snapshot
from src.reporting.league_dashboard import render_league_dashboard
from src.schemas import BOX_SCORE_FIELDS


def validate_refresh(frame, previous, season):
    problems = validate_game_logs(frame)
    if problems:
        raise ValueError('; '.join(problems))
    if _is_synthetic(Path('download.csv'), frame):
        raise ValueError('Synthetic performance data is not allowed')
    numeric = frame[['minutes', *BOX_SCORE_FIELDS]].apply(pd.to_numeric, errors='coerce')
    if not np.isfinite(numeric.to_numpy()).all() or (numeric < 0).any().any():
        raise ValueError('Invalid or missing box-score values')
    dates = pd.to_datetime(frame.game_date, errors='coerce', utc=True)
    if dates.isna().any() or (dates > pd.Timestamp.now(tz='UTC')+pd.Timedelta(days=1)).any():
        raise ValueError('Invalid or future game-log dates')
    if not frame.season.astype(str).eq(season).all():
        raise ValueError('Unexpected season in NBA response')
    if frame[['player_id', 'game_id', 'team', 'opponent']].isna().any().any():
        raise ValueError('Missing game identity')
    # Do not replace a full download with a partial response, even if it looks
    # schema-valid. A source deletion needs review, not automatic acceptance.
    keys = lambda df: set(zip(df.player_id.astype(str), df.game_id.astype(str).str.lstrip('0')))
    if not keys(previous) <= keys(frame):
        raise ValueError('NBA response omits previously loaded player-games; keeping the previous forecast')


def refresh_performance(cfg, source=None, *, download=True, build=build_forecast, make_snapshot=snapshot):
    """Refresh configured training seasons. Never silently change season weights.

    download=False rebuilds from existing reviewed inputs (for model upgrades).
    Network failure is not a trigger to relabel cached data as newly downloaded.
    """
    from src.league.refresh_server import atomic_bytes
    root, output = league_dir(cfg), cfg.paths['outputs']
    lock = root / 'refresh.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError('Another league or performance refresh is running; retry after it finishes') from exc
    os.close(fd)
    try:
        from src.ingestion.csv_source import discover_seasons
        target_season = cfg.league.raw.get('meta', {}).get('season')
        seasons = discover_seasons(cfg.paths['raw'], before=target_season)[-3:]
        if not seasons:
            seasons = sorted(cfg.model['projection']['season_weights'], reverse=True)
        if not seasons or any(not re.fullmatch(r'\d{4}-\d{2}', s) for s in seasons):
            raise ValueError('Configure valid NBA training seasons first')
        names = ['league_player_values.csv', 'league_forecast.json', 'league_lock_advisor.json', 'league_dashboard.html']
        # Guard all mutable inputs and published outputs against concurrent CLI edits.
        watched = [output/n for n in names] + [root/'current.json', root/'sleeper_events.jsonl']
        watched += list((cfg.paths['repo']/'config').glob('*.yaml'))
        watched += [cfg.paths['raw']/s/'current.json' for s in list(seasons) + ([target_season] if target_season else [])]
        watched += [p for s in seasons for p in game_log_files(cfg.paths['raw']/s)]
        watched += list((cfg.paths['repo']/'data/external').glob('*.csv'))
        before = {p:p.read_bytes() if p.exists() else None for p in watched}
        old_data = {}
        if before[output/'league_dashboard.html']:
            match = re.search(r'<script id="league-data" type="application/json">(.*?)</script>', before[output/'league_dashboard.html'].decode('utf-8'), re.S)
            if match:
                old_data = json.loads(match.group(1))
        with TemporaryDirectory(prefix='nba-performance-') as directory:
            stage = Path(directory)
            stage_cfg = replace(cfg, paths={**cfg.paths, 'repo':stage, 'raw':stage/'data/raw',
                                           'external':stage/'data/external', 'outputs':stage/'outputs'})
            shutil.copytree(cfg.paths['repo']/'config', stage/'config')
            shutil.copytree(cfg.paths['repo']/'data/external', stage/'data/external')
            shutil.copytree(root, league_dir(stage_cfg), ignore=shutil.ignore_patterns('*.lock'))
            if not download:
                shutil.copytree(cfg.paths['raw'], stage_cfg.paths['raw'])
            elif target_season and (cfg.paths['raw']/target_season).is_dir():
                # Archived current-season games update projections; keep them in the rebuild.
                shutil.copytree(cfg.paths['raw']/target_season, stage_cfg.paths['raw']/target_season)
            reader = CSVSource(cfg.paths['raw'])
            source = source or NBAApiSource(timeout=30)
            job = uuid.uuid4().hex
            pointers, archives, downloads = {}, {}, []
            for season in seasons:
                previous = reader.fetch_game_logs(season)
                if not download:
                    continue
                frame = source.fetch_game_logs(season)
                validate_refresh(frame, previous, season)
                relative = Path('snapshots')/(job+'.csv')
                staged = stage_cfg.paths['raw']/season/relative
                write_raw(frame, staged, source.name, 'player_game_logs', season)
                item = {'file':relative.as_posix(), 'sha256':hashlib.sha256(staged.read_bytes()).hexdigest()}
                staged_pointer = stage_cfg.paths['raw']/season/'current.json'
                staged_pointer.write_text(json.dumps(item), encoding='utf-8')
                pointers[cfg.paths['raw']/season/'current.json'] = staged_pointer.read_bytes()
                for path in (staged, staged.with_suffix('.csv.manifest.json')):
                    archives[cfg.paths['raw']/season/relative.with_name(path.name)] = path
                downloads.append({'season':season, 'rows':len(frame), 'history_through':str(pd.to_datetime(frame.game_date).max().date())})
            build(stage_cfg, seasons)
            meta_path = stage_cfg.paths['outputs']/'league_forecast.json'
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            for entry in meta['sources']:
                path = Path(entry['path'])
                if path.is_relative_to(stage):
                    entry['path'] = str(cfg.paths['repo']/path.relative_to(stage))
            # Download time is distinct from model generation time.
            if download:
                meta['performance_refreshed_at'] = datetime.now(timezone.utc).isoformat()
            else:
                meta['performance_refreshed_at'] = old_data.get('forecast', {}).get('performance_refreshed_at')
            meta['performance_refresh_seasons'] = seasons
            meta_path.write_text(json.dumps(meta, indent=2), encoding='utf-8')
            data = make_snapshot(stage_cfg)
            # Fail only on coverage the published dashboard did not already lack,
            # so a known roster issue (e.g. a Sleeper free agent) cannot block every rebuild.
            old_issues = {t['team_id']: set(t['issues']) for t in old_data.get('teams', [])}
            new_issues = [t['manager'] + ': ' + issue for t in data['teams'] for issue in t['issues']
                          if issue not in old_issues.get(t['team_id'], set())]
            if not data['verified'] or new_issues:
                raise ValueError('Rebuilt forecast lacks complete verified active-roster coverage'
                                 + (': ' + '; '.join(new_issues) if new_issues else ''))
            if 'last_refresh' in old_data:
                data['last_refresh'] = old_data['last_refresh']
            render_league_dashboard(data, stage_cfg.paths['outputs']/'league_dashboard.html')
            if any((p.read_bytes() if p.exists() else None) != b for p,b in before.items()):
                raise ValueError('Project inputs changed during rebuild; retry without concurrent edits')
            for target, source_path in archives.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target)
            publication = {**pointers, **{output/n:(stage_cfg.paths['outputs']/n).read_bytes() for n in names}}
            try:
                for path, content in publication.items():
                    atomic_bytes(path, content)
            except Exception:
                for path in publication:
                    if before[path] is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_bytes(path, before[path])
                raise
            return {'forecast_generated_at':meta['generated_at'], 'performance_refreshed_at':meta['performance_refreshed_at'],
                    'downloaded':download, 'seasons':downloads, 'history_through':meta.get('performance_history_through'),
                    'changes':[], 'note':('Training-season performance refreshed' if download else 'Model rebuilt from existing history')+'; roster ownership, schedule file and rookie priors are unchanged.'}
    finally:
        lock.unlink(missing_ok=True)
