"""Reproducible, dependency-free offline league dashboard; no remote mutations."""
import json
import re
from pathlib import Path
from src.reporting.trade_dashboard import LINEUP_JS


def render_league_dashboard(data, output):
    root = Path(__file__).parent
    # Reviewed public evidence is versioned separately from performance forecasts.
    reviews = root.parents[1] / 'config/trade_reviews.json'
    review_data = json.loads(reviews.read_text(encoding='utf-8')) if reviews.exists() else {}
    review_encoded = json.dumps(review_data, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    template = (root / 'league_template.html').read_text(encoding='utf-8')
    replacements = {'PAYLOAD': encoded, 'MATH': 'const TRADE_REVIEWS = ' + review_encoded + ';\n' + LINEUP_JS + '\n' +
        (root / 'league_math.js').read_text(encoding='utf-8') + '\n' +
        (root / 'league_advisor_math.js').read_text(encoding='utf-8') + '\n' +
        (root / 'league_needs.js').read_text(encoding='utf-8') + '\n' +
        (root / 'league_rankings.js').read_text(encoding='utf-8') + '\n' +
        (root / 'league_trade_context.js').read_text(encoding='utf-8'), 'UI':
        (root / 'league_tools_ui.js').read_text(encoding='utf-8') + '\n' +
        (root / 'league_ui.js').read_text(encoding='utf-8') + '\n' +
        (root / 'league_refresh.js').read_text(encoding='utf-8')}
    document = re.sub(r'__(PAYLOAD|MATH|UI)__', lambda m: replacements[m.group(1)], template)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding='utf-8')
    return output
