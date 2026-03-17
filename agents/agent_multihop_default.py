"""Default multi-hop routing agent plugin.

Naming convention:
- file name starts with agent_
- exposes AGENT_ID, LABEL, and run_agent_turn(request, context)
"""

AGENT_ID = 'multihop_default'
LABEL = 'Multi-Hop Default'
DESCRIPTION = 'Uses lot-implied flow_id with risk_tolerance + weights and logs full telemetry.'
PARAMETERS = {
    'lot_id': 'required',
    'flow_id': 'optional (defaults from lot)',
    'risk_tolerance': 'low|medium|high|critical_only',
    'weights': ['time', 'cost', 'risk', 'capacity'],
    'top_k': '1..5',
}


def run_agent_turn(request, context):
    """Delegate to the built-in multi-hop orchestration callback provided by dashboard context."""
    runner = context['run_default_multihop_agent']
    return runner(
        session_id=int(request['session_id']),
        prompt=str(request['prompt']),
        lot_id=str(request['lot_id']),
        flow_id=request.get('flow_id'),
        lots_df=request['lots_df'],
        constraints_df=request['constraints_df'],
        routing_tables=request['routing_tables'],
        risk_tolerance=request.get('risk_tolerance'),
        weights=request.get('weights'),
        top_k=int(request.get('top_k', 3)),
        risk_by_site=request.get('risk_by_site') or {},
        agent_id=AGENT_ID,
    )
