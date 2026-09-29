import contextlib,io,time,json,resource
from clarity.models import models_root
from clarity.certification.strict_extract import extract_equation_model
from clarity.certification.lazy_graph import compile_graph
from clarity.certification.type_facts import KINDS
for name in ('thermostat','cruise-controller-model','mixing-sysml-model'):
 start=time.monotonic()
 with contextlib.redirect_stdout(io.StringIO()):model=extract_equation_model(str(models_root()/name/'model.sysml'))
 program=compile_graph(model.execution,.1,specialize=True)
 facts=program['type_evidence']['facts']
 values=[r for rows in facts.values() for r in rows if r['field']=='value']
 result={'model':name,'seconds':time.monotonic()-start,'generic_equations':program['type_evidence']['generic_equations'],'specialized_equations':program['equations'],'scalar_node_fields':len(values),'fixed':sum(len(r['values'])==1 for r in values),'narrowed':sum(len(r['values'])<5 for r in values),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
 print('TYPE_RESULT '+json.dumps(result),flush=True)
