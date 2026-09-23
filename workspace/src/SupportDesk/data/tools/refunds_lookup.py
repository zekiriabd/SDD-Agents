# GÉNÉRÉ par gen_source_tools.py — NE PAS ÉDITER.
# Source `refunds` (file · store `support_data`) · confiance : trusted.
# Schéma figé : workspace/src/SupportDesk/data/schemas/refunds.schema.json
# Éditer ce fichier est [DATA_TOOL_HAND_EDITED] : corriger la DÉCLARATION de la
# source, puis `gen_source_tools.py --write`. Le code et la déclaration ne peuvent
# pas diverger sans que quelqu'un s'en aperçoive — c'est tout l'intérêt.
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from SupportDesk.data.envelope import lookup_record
from SupportDesk.tools.spec import ToolContext, ToolSpec

SPEC = ToolSpec.from_contract("1-refunds-lookup")

SOURCE = "refunds"
PII_FIELDS: frozenset[str] = frozenset()
UNTRUSTED_FIELDS: frozenset[str] = frozenset()


class Input(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    refund_id: str = Field(description="Identifiant du remboursement (RFD-NNNN).")


class Record(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount: str = Field(description="Montant remboursé, EUR, chaîne à deux décimales (partiel possible).")
    claim_id: str = Field(description="Réclamation à l'origine du remboursement (CLM-NNNN).")
    currency: str = Field(description="Devise — toujours EUR.")
    customer_id: str = Field(description="Client remboursé (CUST-NNNN). Filtre d'identité ; non énumérable.")
    method: str = Field(description="Moyen de versement — toujours celui du paiement d'origine : card, paypal, bank_transfer, gift_card.")
    order_id: str = Field(description="Commande concernée.")
    payment_id: str = Field(description="Paiement d'origine sur lequel le remboursement est ou sera versé.")
    processed_at: str | None = Field(description="Date du versement, UTC ISO 8601. null tant que non versé — ne jamais promettre de date.")
    reason: str = Field(description="Motif : cancelled_before_shipping, lost_in_transit, missing_item, returned_to_sender, late_delivery, damaged…")
    refund_id: str = Field(description="Identifiant du remboursement (RFD-NNNN).")
    requested_at: str = Field(description="Date de la demande, UTC ISO 8601.")
    status: str = Field(description="requested, approved (validé, non versé), processed (versé), rejected.")


class Output(BaseModel):
    model_config = ConfigDict(frozen=True)

    record: Record | None = Field(description="L'enregistrement trouvé, ou null si la clé est inconnue.")
    as_of: str = Field(description="Instantané de la source, ISO 8601 UTC. La donnée a pu changer depuis.")
    stale: bool = Field(description="Vrai si l'instantané dépasse max_staleness_hours : le dire à l'utilisateur.")


async def refunds_lookup(params: Input, *, ctx: ToolContext) -> Output:
    """Lecture par clé. Remboursements déjà DEMANDÉS ou ÉMIS, un par remboursement. Utiliser pour :"""
    return await lookup_record(
        source=SOURCE,
        key=params.refund_id,
        record_model=Record,
        output_model=Output,
        ctx=ctx,
        pii_fields=PII_FIELDS,
        untrusted_fields=UNTRUSTED_FIELDS,
    )
