"""Small acceptance-only extensions of the existing real application scenarios."""

from typing import Any

from pydantic_ai.models import Model

from grievances.deletion_evaluation import DeletionScenario
from grievances.evaluation import IntakeScenario
from grievances.progress_evaluation import ProgressScenario
from preferences.store import PreferenceStore


class AcceptanceScenarios:
    def __init__(self, model: Model) -> None:
        self.model = model

    async def isolation(self) -> dict[str, Any]:
        scenario = IntakeScenario(self.model)
        other = IntakeScenario(self.model)
        await other.turn('Create a refund case called Hidden tablet. Company: Other Shop. My tablet is broken. '
                         'I paid INR 8,765 and want a refund. Order reference: OTHER-OWNER-SECRET.')
        decoys = await other.snapshots()
        scenario.checks['decoy_setup_valid'] = (len(decoys) == 1 and decoys[0]['label'] == 'Hidden tablet'
            and decoys[0]['facts'].get('order_reference') == 'OTHER-OWNER-SECRET'
            and '8765' in decoys[0]['facts'].get('amount_paid', '').replace(',', ''))
        scenario.checks.update({'decoy:' + key: value for key, value in other.checks.items()})
        await scenario.turn('Remember that I prefer concise replies as a lasting preference.', forbidden=('OTHER-OWNER-SECRET',))
        await scenario.turn(scenario.COMPLETE, forbidden=('OTHER-OWNER-SECRET',))
        await scenario.turn(DeletionScenario.BETA, forbidden=('ORDER-ALPHA-Q7', 'OTHER-OWNER-SECRET'))
        await scenario.turn('Select the Lumen kettle case.', forbidden=('ORDER-BETA-R9', 'OTHER-OWNER-SECRET'))
        # IntakeScenario constructs a new ApplicationService on each turn.
        alpha = await scenario.turn('Show the amount I am requesting.', forbidden=('ORDER-BETA-R9', 'OTHER-OWNER-SECRET'))
        await scenario.turn('Select the Cobalt headphones case.', forbidden=('ORDER-ALPHA-Q7', 'OTHER-OWNER-SECRET'))
        beta = await scenario.turn('Show the amount I am requesting.', forbidden=('ORDER-ALPHA-Q7', 'OTHER-OWNER-SECRET'))
        await scenario.turn('Return to general research.', forbidden=('ORDER-ALPHA-Q7', 'ORDER-BETA-R9', 'OTHER-OWNER-SECRET'))
        await scenario.turn('Explain how rainbows form.', forbidden=('ORDER-ALPHA-Q7', 'ORDER-BETA-R9', 'OTHER-OWNER-SECRET'))
        cases = await scenario.service().store.list_cases(scenario.owner)
        scenario.checks.update(
            selected_amounts_correct='2400' in alpha.replace(',', '') and '3100' in beta.replace(',', ''),
            other_owner_absent=all(case.label != 'Hidden tablet' for case in cases),
            preference_survived=any('concise' in p.value.lower() for p in await PreferenceStore().list(scenario.owner)),
            public_boundary_isolated=scenario.public_messages == ['Explain how rainbows form.'],
            general_selection_cleared=(await scenario.service().store.selection(scenario.owner, scenario.conversation)).case_id is None,
        )
        return scenario.result('restart_owner_mode', await scenario.snapshots())

    async def outcomes(self) -> dict[str, Any]:
        variants = {name: await ProgressScenario(self.model, isolate=True).run(name)
                    for name in ('replacement_followup', 'explicit_resolution', 'close_without_resolution')}
        return {'scenario': 'outcome_variants', 'variants': variants,
                'checks': {name + ':' + key: value for name, output in variants.items() for key, value in output['checks'].items()}}
