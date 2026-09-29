"""Required source-preservation obligations, distinct from predicate subproofs.

The existing arithmetic backends prove sampled-point/interval implications.
They do not discharge the source execution correspondence obligations below.
An unimplemented evidence rule cannot authorize full preservation.
"""
from clarity.certification.ordered_execution import source_requirement_inventory, fingerprint
from clarity.sysml.parser import SysMLParser

REQUIRED = (
    'initialization', 'execution_coverage', 'correspondence_preservation',
    'controller_premises', 'property_transfer', 'inductive_preservation',
)


def required_preservation_inventory(model_path):
    parser = SysMLParser(str(model_path)); parser.parse()
    return [{'property': req['name'], 'source_property_sha256': fingerprint(req),
             'obligations': list(REQUIRED)} for req in source_requirement_inventory(parser)]


def verify_preservation_inventory(record, model_path):
    expected = required_preservation_inventory(model_path)
    errors = []
    if not isinstance(record, dict) or record.get('inventory') != expected:
        return {'verified': False, 'errors': ['source preservation obligation inventory is missing or changed'],
                'unproved': expected}
    evidence = record.get('evidence')
    if not isinstance(evidence, dict):
        errors.append('source preservation evidence is malformed')
        evidence = {}
    # Never treat a producer's status or an algebraic subproof as evidence of
    # source coverage. Register a rule only with its independent replay checker.
    if evidence:
        errors.append('source preservation evidence uses an unsupported proof rule')
    return {'verified': False, 'errors': errors, 'unproved': expected}
