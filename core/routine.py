"""Experimental one-call terminal tools; existing services retain execution authority."""
import json

from asgiref.sync import sync_to_async
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, ToolOutput
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from conversations.service import ConversationService
from core import routine_constants as constants
from core.routing import IntentRouter, Route
from core.routine_prompts import ROUTINE_INSTRUCTIONS
from core.tracing import tracing
from core.turns import OwnerTurn
from grievances.mutations import CaseMutation
from grievances.models import CaseMessage
from grievances.schema import FactKey
from grievances.store import CaseStore, CaseView, SelectionView
from preferences.inspection import PreferenceInspection
from preferences.models import SavedPreference
from preferences.runner import PreferenceRunner
from preferences.schema import PreferenceIntent
from preferences.store import Preference, PreferenceStore


class CaseInspection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    fields: list[FactKey] = Field(min_length=1, max_length=16)
    include_preferences: bool = False


class RoutineTools:
    def __init__(self, model: Model) -> None:
        self.model = model
        self.agent: Agent[None, PreferenceIntent | CaseInspection | Route] = Agent(
            model, name='routine_tools', instructions=ROUTINE_INSTRUCTIONS,
            output_type=[ToolOutput(PreferenceIntent, name='manage_preference'),
                         ToolOutput(CaseInspection, name='inspect_case'),
                         ToolOutput(Route, name='delegate')],
            retries=0, model_settings={'max_tokens': constants.OUTPUT_TOKENS})
        self.agent.instrument = tracing.private_instrumentation()

    async def run(self, turn: OwnerTurn, epoch: int) -> str | Route:
        store = CaseStore()
        selection = await store.selection(turn.owner_id, turn.conversation_id)
        current = await store.get_case(turn.owner_id, selection.case_id) if selection.case_id else None
        payload = await IntentRouter.payload(turn)
        payload['selected_case_label'] = current.label if current else None
        payload['has_attachment_evidence'] = bool(current and await CaseMessage.objects.filter(
            case_id=current.id, case__owner_id=turn.owner_id, role='attachment').aexists())
        payload['current_preferences'] = [{'key': p.key, 'value': p.value}
                                         for p in await PreferenceStore().list(turn.owner_id)]
        usage = RunUsage()
        with tracing.operation('orchestrator.routine_tools', {'message': turn.text}, kind='agent', private=True) as span:
            result = await self.agent.run(json.dumps(payload), usage=usage,
                usage_limits=UsageLimits(request_limit=constants.REQUEST_LIMIT), timeout=constants.TIMEOUT_SECONDS)
            tracing.record_output(span, result.output.model_dump())
        proposal = result.output
        if isinstance(proposal, Route):
            return proposal
        if isinstance(proposal, PreferenceIntent):
            if not await store.general_allowed(turn, epoch):
                return 'That request includes case details. Please keep it within the case.'
            return await ConversationService(turn.owner_id, PreferenceRunner(self.model, usage, proposal)).run(turn.text)
        if current is None:
            return 'Which case would you like me to check?'
        return await self.inspect(turn, selection, current, proposal, epoch)

    @staticmethod
    @sync_to_async
    def inspect(turn: OwnerTurn, selection: SelectionView, current: CaseView,
                proposal: CaseInspection, epoch: int) -> str:
        if selection.deletion_epoch != epoch:
            return 'The case changed. Please ask again.'
        with CaseMutation(turn, selection, current, operation='read') as mutation:
            if mutation.failure:
                return 'The case changed. Please ask again.'
            answer = '\n'.join(f'**{key.replace("_", " ").capitalize()}:** {current.facts.get(key, "Not recorded yet")}'
                               for key in dict.fromkeys(proposal.fields))
            if proposal.include_preferences:
                preferences = [Preference(p.key, p.value, p.source_message_id, p.source_suggestion_id)
                               for p in SavedPreference.objects.filter(identity=turn.owner_id).order_by('key')]
                answer += '\n\n' + PreferenceInspection.render(preferences)
            return answer
