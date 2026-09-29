"""Full source-node lowering checked against the independent concrete evaluator."""
import contextlib
import io
import unittest

import z3

from clarity.certification.execution_equations import ExecutionEquations, Configuration, Alias, Binding
from clarity.certification.lazy_expressions import scalar, Term, Status
from clarity.certification.lazy_program import NodeCompiler
from clarity.certification.lazy_storage import StorageSchema
from source_equation_fixture import small_source_model
from validate_lazy_source_operations import decode_status


class Program(unittest.TestCase):
    def solution(self, compiler):
        solver=z3.Solver();solver.set(timeout=3000);solver.add(*compiler.eq.equations)
        self.assertEqual(solver.check(),z3.sat,solver.reason_unknown())
        return solver.model()

    def compare_store(self, compiler, encoded, concrete, checked):
        present = [key for key, cell in encoded.store.cells.items()
                   if z3.is_true(checked.eval(cell.present))]
        present.sort(key=lambda key: checked.eval(encoded.store.cells[key].order).as_long())
        self.assertEqual(present, list(concrete.state), 'scalar dictionary presence/order differs')
        for key,value in concrete.state.items():
            cell=compiler.store.cell(encoded.store,key)
            self.assertTrue(z3.is_true(checked.eval(cell.present)),key)
            kind=1 if isinstance(value,Alias) else 2 if isinstance(value,Binding) else 0
            self.assertEqual(checked.eval(cell.kind).as_long(),kind,key)
            if kind==0:
                self.assertTrue(z3.is_true(checked.eval(cell.value==scalar(value))),
                                (key,value,checked.eval(cell.value)))

    def test_complete_fixture_decision_transition(self):
        model=small_source_model()
        spec=StorageSchema('fixture',(),(),(),(),(),0,1)
        compiler=NodeCompiler(model.execution,.1,schema=spec)
        reference=ExecutionEquations(model.execution,.1)
        concrete=Configuration('initial/entry');encoded=compiler.empty()
        for name in ('initial/entry','decision','response','cycle/entry','update','check','decision'):
            concrete.node=name;reference.step(concrete,action={'go':True})
            result=compiler.lower(name,encoded,action={'go':Term(scalar(True))})
            encoded=result.state;checked=self.solution(compiler)
            self.assertIsNone(decode_status(checked,result.status))
            self.compare_store(compiler,encoded,concrete,checked)
        self.assertTrue(z3.is_true(checked.eval(encoded.inputs['x'][1].value==scalar(1.0))))

    def test_all_model_initializations(self):
        from clarity.models import models_root
        from clarity.certification.strict_extract import extract_equation_model
        for name in ('thermostat','cruise-controller-model','mixing-sysml-model'):
            with self.subTest(model=name),contextlib.redirect_stdout(io.StringIO()):
                model=extract_equation_model(str(models_root()/name/'model.sysml'))
                compiler=NodeCompiler(model.execution,.1)
                reference=ExecutionEquations(model.execution,.1)
                concrete=Configuration(compiler.graph['initial_entry'])
                reference.step(concrete)
                result=compiler.lower(compiler.graph['initial_entry'],compiler.empty())
                checked=self.solution(compiler)
                self.assertIsNone(decode_status(checked,result.status))
                self.compare_store(compiler,result.state,concrete,checked)
            print(name,'initialization equations',len(compiler.eq.equations),flush=True)

    def test_source_paths_through_two_decisions(self):
        from clarity.models import models_root
        from clarity.certification.strict_extract import extract_equation_model
        for name in ('thermostat','cruise-controller-model','mixing-sysml-model'):
            with contextlib.redirect_stdout(io.StringIO()):
                model=extract_equation_model(str(models_root()/name/'model.sysml'))
            compiler=NodeCompiler(model.execution,.1,prefix='trace_'+name)
            reference=ExecutionEquations(model.execution,.1)
            concrete=Configuration(compiler.graph['initial_entry']);encoded=compiler.empty()
            actions={key:{'Boolean':False,'Integer':0,'Real':0.0}[kind]
                     for node in compiler.graph['nodes'].values() if node['operation']=='apply_executed_action'
                     for key,kind in node['data']['output_types'].items()}
            symbolic_actions={key:Term(scalar(value)) for key,value in actions.items()}
            decisions=0;visited=[];claims=[];event_checks=[]
            while decisions<2:
                identity=concrete.node
                reference.step(concrete,action=actions)
                result=compiler.lower(identity,encoded,action=symbolic_actions)
                encoded=result.state;visited.append(compiler.graph['nodes'][identity]['operation'])
                claims.extend((result.status==Status.Success,result.successor==compiler.ids[concrete.node]))
                if result.events:
                    expected=concrete.events[-1]
                    boundary,enabled,rows=result.events[0]
                    self.assertEqual(boundary,expected['boundary'])
                    claims.append(enabled)
                    for key,term in rows:
                        row=expected['statuses'][key]
                        if row['error'] is None:
                            claims.extend((term.status==Status.Success,term.value==scalar(row['status'])))
                        else:event_checks.append((term.status,row['error']))
                if concrete.outcome=='decision':
                    decisions+=1
                    if decisions<2:
                        concrete.node=compiler.graph['nodes'][identity]['successors']['resume']
                        concrete.outcome=None
                if concrete.outcome in ('error','terminal'):
                    self.fail((name,concrete.outcome,concrete.error))
            checked=self.solution(compiler)
            for claim in claims:
                self.assertTrue(z3.is_true(checked.eval(claim)),(name,claim,checked.eval(claim)))
            for status,message in event_checks:
                self.assertEqual(decode_status(checked,status)[1],message)
            self.compare_store(compiler,encoded,concrete,checked)
            print(name,'source steps',len(visited),'equations',len(compiler.eq.equations),
                  'operations',','.join(sorted(set(visited))),flush=True)


if __name__=='__main__':unittest.main()
