"""The same source-bound proof gate for launcher and direct training calls."""
from __future__ import annotations

from pathlib import Path


def require_training_evidence(model_path, dt, reduced_spec_path, safety_certificate_path):
    from clarity.discretization.certificates import load_certificate, verify_certificate
    from clarity.certification.reduced_mdp_spec import load_reduced_mdp_spec, check_reduced_mdp_spec, file_sha256
    from clarity.discretization.timing import canonical_dt
    if reduced_spec_path is None or safety_certificate_path is None:
        raise ValueError('training requires both the checked reduced specification and safety certificate')
    spec_path, safety_path = Path(reduced_spec_path), Path(safety_certificate_path)
    spec = load_reduced_mdp_spec(spec_path)
    errors = check_reduced_mdp_spec(spec)
    cert = load_certificate(safety_path)
    report = verify_certificate(cert, check_files=True)
    errors.extend(report['errors'])
    if report.get('safety_certified') is not True:
        errors.append('original SysML safety preservation is not certified')
    if Path(cert.get('model', {}).get('path', '')).resolve() != Path(model_path).resolve():
        errors.append('training model differs from certified source')
    if cert.get('settings', {}).get('dt') != canonical_dt(dt):
        errors.append('training dt differs from safety certificate')
    if cert.get('reduced_specification', {}).get('file_sha256') != file_sha256(spec_path):
        errors.append('training reduced specification differs from safety certificate')
    if errors:
        raise ValueError('training proof gate rejected: ' + '; '.join(errors))
    return spec, report
