"""Run synthetic grievance experiments through Langfuse."""

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from core.errors import user_error_message
from grievances.evaluation import IntakeExperiment
from grievances.acceptance import AcceptanceExperiment, AcceptanceGate
from grievances.acceptance_catalog import CASE_BY_ID
from grievances.acceptance_evidence import AcceptanceFingerprint, DeterministicEvidence
from grievances.draft_evaluation import DraftExperiment
from grievances.policy_evaluation import PolicyExperiment
from grievances.progress_evaluation import ProgressExperiment
from grievances.deletion_evaluation import DeletionExperiment


class Command(BaseCommand):
    help = 'Run real-model synthetic grievance experiments; no external actions.'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('--scenario', choices=(*CASE_BY_ID, *PolicyExperiment.CASES, *ProgressExperiment.CASES, *DeletionExperiment.CASES), help='Run one dataset item; requires --policy, --progress or --deletion.')
        parser.add_argument('--output', type=Path, help='Write the run or finalized acceptance artifact.')
        parser.add_argument('--deterministic-evidence', type=Path)
        parser.add_argument('--live-evidence', type=Path)
        parser.add_argument('--reviews', type=Path)
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--acceptance', action='store_true', help='Run the twenty-scenario acceptance dataset; semantic review remains pending.')
        group.add_argument('--record-deterministic', type=Path, help='Run controlled pytest checks and record source-bound JUnit evidence; no model calls.')
        group.add_argument('--finalize', type=Path, help='Finalize a saved acceptance artifact without rerunning models.')
        group.add_argument('--fingerprint', action='store_true', help='Print current artifact fingerprint inputs; no model calls.')
        group.add_argument('--deletion', action='store_true', help='Evaluate case deletion and preservation with fictional data.')
        group.add_argument('--progress', action='store_true', help='Evaluate manual sent reports, replies and follow-ups.')
        group.add_argument('--policy', action='store_true', help='Evaluate official policy evidence with fixed HTTP sources.')
        group.add_argument('--drafts', action='store_true', help='Evaluate factual draft versions instead of intake.')

    def handle(self, *args: Any, **options: Any) -> None:
        if options['fingerprint']:
            self.stdout.write(json.dumps(AcceptanceFingerprint.current(), indent=2))
            return
        if options['record_deterministic']:
            try:
                artifact = DeterministicEvidence.record(options['record_deterministic'])
            except (ValueError, OSError) as error:
                raise CommandError(str(error)) from None
            self.stdout.write(json.dumps(artifact, indent=2))
            return
        if options['finalize']:
            if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
                raise CommandError('Configure both Langfuse project keys to publish the bound review scores.')
            if not all(options[key] for key in ('reviews', 'deterministic_evidence', 'live_evidence', 'output')):
                raise CommandError('--finalize requires --reviews, --deterministic-evidence, --live-evidence and --output.')
            try:
                gate = AcceptanceGate()
                artifact = gate.finalize(options['finalize'], options['reviews'], options['deterministic_evidence'], options['live_evidence'])
                gate.publish_review_scores(artifact, options['reviews'])
            except (ValueError, OSError, KeyError, TypeError) as error:
                raise CommandError(str(error)) from None
            options['output'].write_text(json.dumps(artifact, indent=2))
            self.stdout.write(json.dumps(artifact, indent=2))
            return
        if options['acceptance']:
            if not options['deterministic_evidence'] or not options['output']:
                raise CommandError('--acceptance requires --deterministic-evidence and --output.')
            if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
                raise CommandError('Configure both Langfuse project keys first.')
            try:
                artifact = AcceptanceExperiment(options['deterministic_evidence'], options['scenario']).run()
            except Exception as error:
                raise CommandError(user_error_message(error)) from None
            options['output'].write_text(json.dumps(artifact, indent=2))
            self.stdout.write(json.dumps(artifact, indent=2))
            if not artifact['checks_passed']:
                raise CommandError('Acceptance checks failed; inspect saved outputs.')
            return
        if options['scenario'] and not (options['policy'] or options['progress'] or options['deletion']):
            raise CommandError('--scenario requires --policy, --progress or --deletion.')
        if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
            raise CommandError('Configure both Langfuse project keys first.')
        try:
            experiment = (DeletionExperiment(options['scenario']) if options['deletion'] else
                          ProgressExperiment(options['scenario']) if options['progress'] else
                          PolicyExperiment(options['scenario']) if options['policy'] else
                          DraftExperiment() if options['drafts'] else IntakeExperiment())
            report = experiment.run()
        except Exception as error:
            raise CommandError(user_error_message(error)) from None
        self.stdout.write(json.dumps(asdict(report), indent=2))
        if not report.passed:
            raise CommandError('Grievance checks failed; review the experiment outputs.')
