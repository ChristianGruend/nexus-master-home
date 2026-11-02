"""Read-only UI projection of existing Hermes journals and Bridge receipts."""
from __future__ import annotations
import json
from pathlib import Path
from nexus.common.paths import ROOT, WORKSPACE


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _recent(store: Path, pattern: str, limit: int) -> list[dict]:
    files = []
    for path in store.glob(pattern):
        try:
            files.append((path.stat().st_mtime, path))
        except OSError:
            continue
    rows = []
    for _, path in sorted(files, key=lambda item: item[0], reverse=True)[:limit]:
        data = _load(path)
        if data:
            rows.append(dict(data, name=path.name))
    return rows


def _order(data):
    batches = data.get('batches') or []
    checks = list(data.get('verification') or [])
    ids = list(data.get('task_ids') or [])
    for entry in data.get('trace') or []:
        result = entry.get('result') or {}
        ids.append(result.get('task_id'))
    errors = [data.get('error')] if data.get('error') else []
    for batch in batches:
        checks.extend(batch.get('verification') or [])
        ids.extend(batch.get('task_ids') or [])
        if batch.get('error'):
            errors.append(batch['error'])
    for check in checks:
        ids.extend([check.get('create_task_id'), check.get('read_task_id')])
    for check in data.get('static') or []:
        ids.append(check.get('task_id'))
        if check.get('error'):
            errors.append(check['error'])
    run = data.get('run')
    if isinstance(run, dict):
        ids.append(run.get('task_id'))
        if run.get('error'):
            errors.append(run['error'])
    report = data.get('report') or {}
    if isinstance(report, dict):
        ids.extend(report.get('task_ids') or [])
        checks.extend(report.get('verification') or [])
        if report.get('error'):
            errors.append(report['error'])
    requirements = data.get('requirements') or data.get('files') or []
    paths = [r.get('path') for r in requirements if isinstance(r, dict)]
    artifacts = []
    for path in dict.fromkeys(paths + [c.get('path') for c in checks]):
        if not path:
            continue
        check = next((c for c in checks if c.get('path') == path and c.get('equal') is True), {})
        artifacts.append({'path': path, 'verified': bool(check), 'sha256': check.get('sha256')})
    return {'name': data.get('name'), 'task': data.get('task') or data.get('goal') or '',
            'status': data.get('status') or 'UNKNOWN', 'phase': data.get('phase'),
            'run_id': data.get('run_id') or data.get('plan_id'),
            'updated_at': data.get('updated_at') or data.get('finished_at') or data.get('created_at'),
            'error': '; '.join(dict.fromkeys(str(e) for e in errors)),
            'task_ids': list(dict.fromkeys(i for i in ids if isinstance(i, str))),
            'artifacts': artifacts, 'completed': sum(a['verified'] for a in artifacts), 'total': len(artifacts)}


def orders_snapshot(root=ROOT, workspace=WORKSPACE):
    receipts = _recent(workspace / 'bridge/tasks', 'NX-*.json', 20)
    approvals = []
    approval_error = None
    try:
        from nexus.bridge.approvals import approvals as approval_store
        approvals = [{k: a.to_dict().get(k) for k in ('approval_id', 'task', 'status', 'expires_at')}
                     for a in approval_store().list(limit=20, status='pending')]
    except Exception:
        approval_error = 'APPROVAL_STATUS_UNAVAILABLE'
    active = _load(workspace / 'state/nexus_active_task.json')
    return {
        'active': {k: active.get(k) for k in ('status', 'phase', 'run_id', 'error')} if active else None,
        'orders': [_order(d) for d in _recent(workspace / 'state', 'hermes-order-*.json', 6)],
        'project_orders': [_order(d) for d in _recent(workspace / 'state', 'hermes-project-order-*.json', 6)],
        'code_orders': [_order(d) for d in _recent(workspace / 'state', 'hermes-code-order-*.json', 6)],
        'receipts': [{'id': r.get('id'), 'task': r.get('task'), 'status': r.get('status'),
                      'path': (r.get('params') or {}).get('path'), 'error_code': r.get('error_code'),
                      'created_at': r.get('created_at'), 'updated_at': r.get('updated_at')}
                     for r in receipts],
        'pending_approvals': approvals, 'pending_count': len(approvals) if not approval_error else None,
        'approval_error': approval_error, 'source': 'persisted_journals_and_bridge_receipts',
    }
