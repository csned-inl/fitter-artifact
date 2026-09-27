"""Small source-graph fixtures for the general recursive equation backend."""
from clarity.certification.equations import EquationModel
from clarity.certification.ordered_execution import fingerprint


def small_source_model():
    ref=lambda name:{'kind':'reference','path':['system',name]}
    literal=lambda value:{'kind':'literal','value':value}
    nodes={}
    def node(name,operation,next=None,**data):
        nodes[name]={'operation':operation,'data':data,'successors':{} if next is None else {'next':next},
                     'on_exception':'execution_error'}
    constraints={'constraints':[],'bindings':[],'flows':[], 'algorithm':{'iteration_limit':2}}
    node('initial/entry','initialize_existing_runtime','decision',parameters=[
        {'qualified_name':'system::x','cli_name':'x','value':0.0}],machine_initial_states={},
        stored_aliases={},values=[],constraints=constraints)
    node('decision','decision',context='system',inputs={'x':ref('x'),'done':literal(False)},
         expected_inputs=['x','done'],completion_input='done')
    nodes['decision']['successors']={'resume':'response'}
    node('response','apply_executed_action','cycle/entry',outputs={'go':'system::go'},output_types={'go':'Boolean'})
    node('cycle/entry','begin_cycle','update')
    node('update','assign','check',context='system',target='system::x',source_storage_target='system::x',source_target=['x'],
         expression={'kind':'conditional','condition':ref('go'),
                     'true':{'kind':'binary','operator':'+','left':ref('x'),'right':literal(1.0)},'false':ref('x')})
    node('check','check_all_requirements','decision',boundary='cycle_end',properties=['nonnegative'],
         expressions={'nonnegative':{'kind':'binary','operator':'>=','left':ref('x'),'right':literal(0.0)}})
    node('execution_error','outcome',outcome='execution_error')
    graph={'system_part':'system','reference_bindings':{},'nodes':nodes,'machine_states':{},'initial_transition_targets':[],
           'initial_entry':'initial/entry','cycle_entry':'cycle/entry','part_attribute_types':{'system':{'x':'Real','go':'Boolean'}},
           'scenario_profile':{'bounds':{},'state_bindings':[]},
           'storage':{'system::x':{'writers':['initial/entry','update']}}}
    graph['sha256']=fingerprint(graph)
    return EquationModel('test-only source graph',{'x'},{'go'},
        execution={'decision_transition':graph,'property_inventory':[{'name':'nonnegative','tags':['Prohibition']}]},
        value_semantics={'decision_updates':{'x':[{'runtime_key':'system::x'}]}})
